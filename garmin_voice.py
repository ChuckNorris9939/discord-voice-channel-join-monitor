import os
import sys
import time
import wave
import threading
import difflib
import logging
import asyncio
import queue
from typing import Final
from pathlib import Path

import discord
import speech_recognition as sr
from discord.ext import voice_recv

try:
    import vosk  # optional, only needed for offline STT
except ImportError:
    vosk = None

# --------------------------------------------------
# Logging setup (honours APP_TESTING_MODE and LOG_LEVEL)
# --------------------------------------------------
logger = logging.getLogger(__name__)

def _init_logging():
    """Set log level:
    - LOG_LEVEL=DEBUG  → always DEBUG
    - else APP_TESTING_MODE=true/1/yes → DEBUG
    - default INFO"""
    default_level = logging.INFO
    testing = os.getenv("APP_TESTING_MODE", "false").lower() in ("1", "true", "yes")
    env_level = os.getenv("LOG_LEVEL", "").upper()
    if env_level == "DEBUG":
        default_level = logging.DEBUG
    elif testing:
        default_level = logging.DEBUG

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=default_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stdout,
        )
    logger.setLevel(default_level)
    logger.debug("Logger initialised at %s", logging.getLevelName(default_level))

_init_logging()

# ==================================================
# Configurable Speech‑to‑Text backend
# ==================================================
STT_ENABLED: Final[bool] = os.getenv("STT_ENABLED", "true").lower() in ("1", "true", "yes")
STT_ENGINE: Final[str] = os.getenv("STT_ENGINE", "google").lower()
VOSK_MODEL_PATH: Final[str] = os.getenv("VOSK_MODEL_PATH", "vosk-model-de")

# --------------------------------------------------
# Audio / Recording constants (configurable via environment)
# --------------------------------------------------
RECORD_SECONDS: Final[int] = int(os.getenv("GARMIN_RECORD_SECONDS", "600"))  # 10 min default
SAMPLERATE: Final[int] = int(os.getenv("GARMIN_SAMPLERATE", "48000"))  # Discord standard
CHANNELS: Final[int] = int(os.getenv("GARMIN_CHANNELS", "2"))  # stereo
BYTES_PER_SAMPLE: Final[int] = int(os.getenv("GARMIN_BYTES_PER_SAMPLE", "2"))  # 16‑bit
MAX_BUFFER_SIZE: Final[int] = RECORD_SECONDS * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE

FRAMES_PER_BUFFER: Final[int] = int(os.getenv("GARMIN_FRAMES_PER_BUFFER", "960"))  # 20 ms @48 kHz
CHUNK_SIZE: Final[int] = FRAMES_PER_BUFFER * CHANNELS * BYTES_PER_SAMPLE

# --------------------------------------------------
# Buffer management for stuttering prevention
# --------------------------------------------------
BUFFER_PREFILL_THRESHOLD: Final[int] = int(os.getenv("GARMIN_BUFFER_PREFILL", "384000"))  # Start processing after 8s of audio
BUFFER_OVERFLOW_THRESHOLD: Final[float] = float(os.getenv("GARMIN_BUFFER_OVERFLOW", "0.6"))  # 60% of max buffer size
PROCESS_INTERVAL_S: Final[float] = float(os.getenv("GARMIN_PROCESS_INTERVAL", "3.0"))  # Much less frequent processing

# --------------------------------------------------
# Additional audio processing constants for stuttering prevention
# --------------------------------------------------
WINDOW_BYTES_MIN: Final[int] = int(os.getenv("GARMIN_WINDOW_MIN", "48000"))  # Minimum bytes for STT (1 second)
WINDOW_BYTES_MAX: Final[int] = int(os.getenv("GARMIN_WINDOW_MAX", "240000"))  # Maximum bytes for STT (5 seconds)

# --------------------------------------------------
# Audio pipeline health monitoring
# --------------------------------------------------
AUDIO_CALLBACK_TIMEOUT: Final[float] = float(os.getenv("GARMIN_AUDIO_CALLBACK_TIMEOUT", "5.0"))  # Max time between audio callbacks
MIN_AUDIO_CHUNK_SIZE: Final[int] = int(os.getenv("GARMIN_MIN_AUDIO_CHUNK", "1920"))  # Minimum expected chunk size (20ms @ 48kHz stereo)
MAX_AUDIO_CHUNK_SIZE: Final[int] = int(os.getenv("GARMIN_MAX_AUDIO_CHUNK", "9600"))  # Maximum expected chunk size (100ms @ 48kHz stereo)

# --------------------------------------------------
# Recording management constants
# --------------------------------------------------
RECORDING_RESTART_DELAY: Final[float] = float(os.getenv("GARMIN_RECORDING_RESTART_DELAY", "1.0"))
MAX_RECORDING_DURATION: Final[int] = int(os.getenv("GARMIN_MAX_RECORDING_DURATION", "3600"))  # 1 hour
BUFFER_MONITOR_INTERVAL: Final[float] = float(os.getenv("GARMIN_BUFFER_MONITOR_INTERVAL", "30.0"))
MAX_RECORDING_ERRORS: Final[int] = int(os.getenv("GARMIN_MAX_RECORDING_ERRORS", "5"))

# --------------------------------------------------
# Trigger phrase detection
# --------------------------------------------------
SOUND_DING      = "sounds/garmin_ding.wav"
SOUND_DINGDING  = "sounds/garmin_dingding.wav"

TRIGGERS = [
    {  # wake word
        "name": "ding",
        "phrase": "okay garmin",
        "threshold": 0.7,
        "sound": SOUND_DING,
        "save": False,
    },
    {  # follow‑up within 5 s after wake word
        "name": "save",
        "phrase": "video speichern",
        "threshold": 0.7,
        "sound": SOUND_DINGDING,
        "save": True,
    },
]

TRIGGER_COOLDOWN_S: Final[int]   = 5
RECOGNITION_WINDOW_S: Final[float] = 3.0
MIN_WINDOW_S:        Final[float] = 0.7
WINDOW_BYTES_MAX: Final[int] = int(RECOGNITION_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
WINDOW_BYTES_MIN: Final[int] = int(MIN_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)

PROCESS_INTERVAL_S: Final[float] = 1.0
OUTPUT_DIR: Final[str] = "garmin-output"


class GarminVoiceManager:
    """Voice listener that reacts on trigger phrases and plays sounds."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.audio_buffer = bytearray()
        self._buf_lock   = threading.Lock()

        # STT processing queue to prevent conflicts
        self.stt_queue = queue.Queue(maxsize=2)
        self.stt_worker_thread = None
        self.stt_worker_running = False

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True

        # Optional Vosk model
        self.vosk_model = None
        if STT_ENGINE == "vosk":
            if vosk is None:
                raise RuntimeError("STT_ENGINE='vosk' but 'vosk' package missing.")
            model_path = Path(VOSK_MODEL_PATH)
            if not model_path.exists():
                raise FileNotFoundError(f"Vosk model not found at '{model_path}'.")
            logger.info("Loading Vosk model from %s …", model_path)
            self.vosk_model = vosk.Model(str(model_path))
            logger.info("Vosk model loaded.")

        self.is_processing = False
        self.last_process_time = 0.0
        self.last_trigger_time = {t["name"]: 0.0 for t in TRIGGERS}
        self._last_ok_time: float = 0.0  # timestamp of last "okay garmin"
        self._last_stt_text: str = ""  # suppress duplicates

        # Recording management attributes
        self.recording_start_time: float = 0.0
        self.last_buffer_check: float = 0.0
        self.recording_errors: int = 0
        self.max_errors: int = MAX_RECORDING_ERRORS
        self.buffer_monitor_task: asyncio.Task | None = None

        # Audio pipeline health monitoring
        self.last_audio_callback_time: float = 0.0
        self.audio_callback_count: int = 0
        self.audio_callback_errors: int = 0
        self.last_chunk_size: int = 0
        self.audio_pipeline_healthy: bool = True

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.vc: voice_recv.VoiceRecvClient | None = None

    # ------------------------- Discord voice callbacks -------------------------
    def callback(self, user: discord.User | None, data: voice_recv.VoiceData):
        """Callback for incoming audio data with improved buffer management and health monitoring."""
        now = time.time()
        
        # Audio pipeline health monitoring
        self.audio_callback_count += 1
        chunk_size = len(data.pcm)
        self.last_chunk_size = chunk_size
        
        # Validate audio chunk size
        if chunk_size < MIN_AUDIO_CHUNK_SIZE or chunk_size > MAX_AUDIO_CHUNK_SIZE:
            self.audio_callback_errors += 1
            logger.warning(f"Invalid audio chunk size: {chunk_size} bytes (expected {MIN_AUDIO_CHUNK_SIZE}-{MAX_AUDIO_CHUNK_SIZE})")
            self.audio_pipeline_healthy = False
        
        # Check for audio callback timeouts (indicates stuttering)
        if self.last_audio_callback_time > 0:
            time_since_last = now - self.last_audio_callback_time
            if time_since_last > AUDIO_CALLBACK_TIMEOUT:
                self.audio_callback_errors += 1
                logger.warning(f"Audio callback timeout: {time_since_last:.2f}s since last callback")
                self.audio_pipeline_healthy = False
        
        self.last_audio_callback_time = now
        
        # Append to buffer with validation
        try:
            self._append_to_buffer(data.pcm)
        except Exception as e:
            self.audio_callback_errors += 1
            logger.error(f"Error appending audio to buffer: {e}")
            self.audio_pipeline_healthy = False
            return
        
        # Skip STT processing if disabled
        if not STT_ENABLED:
            return
        
        # Only process if we have enough audio data and enough time has passed
        with self._buf_lock:
            buffer_size = len(self.audio_buffer)
        
        if (buffer_size >= BUFFER_PREFILL_THRESHOLD and 
            (now - self.last_process_time >= PROCESS_INTERVAL_S)):
            
            # Use queue-based processing to prevent conflicts
            try:
                if not self.stt_queue.full():
                    self.stt_queue.put_nowait(now)
                    self.last_process_time = now
                    logger.debug(f"Queued STT processing request (buffer: {buffer_size} bytes)")
            except queue.Full:
                logger.debug("STT queue full, skipping processing request")

    def _append_to_buffer(self, chunk: bytes):
        """Append audio data to buffer with improved overflow management."""
        with self._buf_lock:
            self.audio_buffer.extend(chunk)
            
            # More aggressive buffer management to prevent stuttering
            current_size = len(self.audio_buffer)
            max_size = int(MAX_BUFFER_SIZE * BUFFER_OVERFLOW_THRESHOLD)
            
            if current_size > max_size:
                # Remove oldest data, keeping the most recent
                excess = current_size - max_size
                del self.audio_buffer[:excess]
                logger.debug(f"Buffer overflow prevented: removed {excess} bytes")

    # ------------------------------ Speech‑rec thread ---------------------------
    def _start_stt_worker(self):
        """Start the STT worker thread."""
        if not self.stt_worker_running:
            self.stt_worker_running = True
            self.stt_worker_thread = threading.Thread(target=self._stt_worker, daemon=True)
            self.stt_worker_thread.start()
            logger.debug("STT worker thread started")

    def _stop_stt_worker(self):
        """Stop the STT worker thread."""
        self.stt_worker_running = False
        if self.stt_worker_thread and self.stt_worker_thread.is_alive():
            self.stt_worker_thread.join(timeout=5)
            logger.debug("STT worker thread stopped")

    def _stt_worker(self):
        """Worker thread for STT processing."""
        while self.stt_worker_running:
            try:
                # Wait for processing request
                request_time = self.stt_queue.get(timeout=1.0)
                self._process_audio_data(request_time)
                self.stt_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in STT worker: {e}")
                self.recording_errors += 1

    def _process_audio_data(self, request_time: float):
        """Process audio data for speech recognition with improved buffer handling."""
        self.is_processing = True
        tmp_path = None
        try:
            # Get a copy of the buffer for processing
            with self._buf_lock:
                buf_copy = bytes(self.audio_buffer)
            
            # Ensure we have enough data to process
            if len(buf_copy) < WINDOW_BYTES_MIN:
                logger.debug(f"Insufficient audio data: {len(buf_copy)} < {WINDOW_BYTES_MIN}")
                return
            
            # Use a sliding window approach for better continuity
            window_size = min(len(buf_copy), WINDOW_BYTES_MAX)
            window = buf_copy[-window_size:]
            
            logger.debug(f"Processing audio window: {len(window)} bytes ({len(window)/SAMPLERATE/CHANNELS/BYTES_PER_SAMPLE:.2f}s)")

            # --- Speech‑to‑Text -------------------------------------------------
            if STT_ENGINE == "vosk":
                import audioop, json
                mono = audioop.tomono(window, BYTES_PER_SAMPLE, 0.5, 0.5)
                pcm16k, _ = audioop.ratecv(mono, BYTES_PER_SAMPLE, 1, SAMPLERATE, 16_000, None)
                rec = vosk.KaldiRecognizer(self.vosk_model, 16_000)
                rec.AcceptWaveform(pcm16k)
                text = json.loads(rec.Result()).get("text", "")
            else:  # Google
                import concurrent.futures, socket
                socket.setdefaulttimeout(8)
                tmp_path = os.path.join(OUTPUT_DIR, f"temp_{int(time.time()*1000)}.wav")
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(BYTES_PER_SAMPLE)
                    wf.setframerate(SAMPLERATE)
                    wf.writeframes(window)

                with sr.AudioFile(tmp_path) as source:
                    audio = self.recognizer.record(source)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(self.recognizer.recognize_google, audio, language="de-DE")
                    try:
                        text = future.result(timeout=8)
                    except concurrent.futures.TimeoutError:
                        logger.warning("Google STT timeout – skipping chunk")
                        text = ""

            # -------------------------------------------------------------------
            text = text.lower().strip()
            # Skip if identical to previous STT result (prevents duplicates)
            if text == self._last_stt_text:
                return
            self._last_stt_text = text
            if text:
                logger.debug("STT[%s]: '%s'", STT_ENGINE, text)
            else:
                return

            now = time.time()
            for trig in sorted(TRIGGERS, key=lambda t: len(t["phrase"]), reverse=True):
                phrase = trig["phrase"]
                # Substring first
                matched = phrase in text or difflib.SequenceMatcher(None, phrase, text).ratio() >= trig["threshold"]
                if not matched:
                    continue

                # Additional gating: "save" only valid within 5 s after wake word
                if trig["name"] == "save":
                    if now - self._last_ok_time > 5:
                        continue  # too late

                # Cooldown per trigger
                if now - self.last_trigger_time[trig["name"]] < TRIGGER_COOLDOWN_S:
                    continue

                # Fire trigger
                self.last_trigger_time[trig["name"]] = now
                if trig["name"] == "ding":
                    self._last_ok_time = now  # remember wake word time

                if trig["save"]:
                    self.save_recording()
                self.play_sound(trig["sound"])
                break
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            logger.error("Google STT request error: %s", e)
            self.recording_errors += 1
        except Exception as e:
            logger.error("Unexpected error during STT processing: %s", e)
            self.recording_errors += 1
        finally:
            if tmp_path and os.path.isfile(tmp_path):
                os.remove(tmp_path)
            self.is_processing = False

    # ------------------------------ Helpers ------------------------------------
    def save_recording(self):
        """Save current recording and restart with a delay to prevent stuttering."""
        try:
            # Stop current recording monitoring
            if self.buffer_monitor_task and not self.buffer_monitor_task.done():
                self.buffer_monitor_task.cancel()
                logger.debug("Cancelled buffer monitor task for save operation")
            
            filename = f"garmin_recording_{int(time.time())}.wav"
            path = os.path.join(OUTPUT_DIR, filename)
            
            with self._buf_lock:
                data = bytes(self.audio_buffer)
            
            with wave.open(path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(BYTES_PER_SAMPLE)
                wf.setframerate(SAMPLERATE)
                wf.writeframes(data)
            
            logger.info("Recording saved: %s", path)
            
            # Clear buffer and restart recording
            with self._buf_lock:
                self.audio_buffer.clear()
            
            # Schedule restart of recording
            if self.vc and self.vc.is_connected():
                asyncio.create_task(self._restart_recording_after_save())
                
        except Exception as e:
            logger.error("Error during save_recording: %s", e)
            self.recording_errors += 1

    def play_sound(self, filepath: str):
        if self.vc and os.path.isfile(filepath):
            self.vc.play(discord.FFmpegPCMAudio(filepath))
        else:
            logger.warning("Sound file '%s' not found or VC is None", filepath)

    # ---------------------- Voice connection helpers ---------------------------
    async def join_channel(self, channel: discord.VoiceChannel):
        """Connect to a voice channel and start listening. Disconnect first if already connected."""
        if self.vc:
            await self.leave_channel()
        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        self.vc.listen(voice_recv.BasicSink(self.callback))
        
        # Only start STT worker if STT is enabled
        if STT_ENABLED:
            self._start_stt_worker()
            logger.info("🔊 Joined voice channel '%s' (STT engine: %s)", channel.name, STT_ENGINE)
        else:
            logger.info("🔊 Joined voice channel '%s' (STT disabled)", channel.name)
        
        await self._start_recording()

    async def leave_channel(self):
        """Disconnect from the current voice connection (if any)."""
        await self._stop_recording()
        self._stop_stt_worker()
        if self.vc:
            await self.vc.disconnect()
            logger.info("🔇 Left voice channel")
            self.vc = None

    # ------------------------- Recording Management -------------------------
    async def _start_recording(self):
        """Initialize recording state and start monitoring."""
        self.recording_start_time = time.time()
        self.last_buffer_check = time.time()
        self.recording_errors = 0
        logger.debug("Recording started - monitoring buffer health")
        
        # Start buffer monitoring task
        if self.buffer_monitor_task and not self.buffer_monitor_task.done():
            self.buffer_monitor_task.cancel()
        self.buffer_monitor_task = asyncio.create_task(self._monitor_recording_buffer())

    async def _stop_recording(self):
        """Stop recording monitoring."""
        if self.buffer_monitor_task and not self.buffer_monitor_task.done():
            self.buffer_monitor_task.cancel()
            try:
                await self.buffer_monitor_task
            except asyncio.CancelledError:
                pass
            logger.debug("Recording monitoring stopped")

    async def _restart_recording_after_save(self):
        """Restart recording after a save operation with delay."""
        await asyncio.sleep(RECORDING_RESTART_DELAY)
        if self.vc and self.vc.is_connected():
            await self._start_recording()
            logger.debug("Recording restarted after save operation")

    async def _monitor_recording_buffer(self):
        """Monitor recording health and restart if needed."""
        while True:
            try:
                await asyncio.sleep(BUFFER_MONITOR_INTERVAL)
                
                current_time = time.time()
                recording_duration = current_time - self.recording_start_time
                buffer_size = len(self.audio_buffer)
                
                # Check for issues that require restart
                needs_restart = False
                restart_reason = ""
                
                # Check recording duration
                if recording_duration > MAX_RECORDING_DURATION:
                    needs_restart = True
                    restart_reason = f"max duration exceeded ({recording_duration:.1f}s)"
                
                # Check buffer size
                elif buffer_size > MAX_BUFFER_SIZE:
                    needs_restart = True
                    restart_reason = f"buffer overflow ({buffer_size} bytes)"
                
                # Check error count
                elif self.recording_errors >= self.max_errors:
                    needs_restart = True
                    restart_reason = f"too many errors ({self.recording_errors})"
                
                # Check audio pipeline health
                elif not self.audio_pipeline_healthy:
                    needs_restart = True
                    restart_reason = f"audio pipeline unhealthy (errors: {self.audio_callback_errors})"
                
                # Check for audio callback timeouts (indicates stuttering)
                elif (self.last_audio_callback_time > 0 and 
                      current_time - self.last_audio_callback_time > AUDIO_CALLBACK_TIMEOUT):
                    needs_restart = True
                    restart_reason = f"audio callback timeout ({current_time - self.last_audio_callback_time:.1f}s)"
                
                # Check if buffer is empty for too long (potential connection issue)
                elif buffer_size == 0 and (current_time - self.last_buffer_check) > 60:
                    needs_restart = True
                    restart_reason = "empty buffer for too long"
                
                if needs_restart:
                    logger.warning(f"Recording health check failed: {restart_reason}. Restarting...")
                    await self._restart_recording()
                else:
                    # Log detailed health status
                    audio_status = "healthy" if self.audio_pipeline_healthy else "unhealthy"
                    time_since_audio = current_time - self.last_audio_callback_time if self.last_audio_callback_time > 0 else 0
                    logger.debug(f"Recording health OK - duration: {recording_duration:.1f}s, buffer: {buffer_size} bytes, "
                               f"errors: {self.recording_errors}, audio: {audio_status}, "
                               f"audio_errors: {self.audio_callback_errors}, time_since_audio: {time_since_audio:.1f}s")
                    
            except asyncio.CancelledError:
                logger.debug("Buffer monitoring task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in buffer monitoring: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def _restart_recording(self):
        """Restart recording by clearing state and buffer."""
        logger.info("Restarting recording system...")
        
        # Reset recording state
        self.recording_start_time = time.time()
        self.last_buffer_check = time.time()
        self.recording_errors = 0
        
        # Reset audio pipeline health
        self.audio_pipeline_healthy = True
        self.audio_callback_errors = 0
        self.last_audio_callback_time = 0.0
        
        # Clear audio buffer
        with self._buf_lock:
            self.audio_buffer.clear()
        
        logger.info("Recording system restarted")

    def get_recording_health(self) -> dict:
        """Get current recording system health status."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time
        
        with self._buf_lock:
            buffer_size = len(self.audio_buffer)
        
        # Calculate audio pipeline metrics
        time_since_audio = current_time - self.last_audio_callback_time if self.last_audio_callback_time > 0 else 0
        audio_callback_rate = self.audio_callback_count / max(recording_duration, 1) if recording_duration > 0 else 0
        
        return {
            "connected": self.vc is not None and self.vc.is_connected(),
            "recording_duration": recording_duration,
            "buffer_size": buffer_size,
            "recording_errors": self.recording_errors,
            "max_errors": self.max_errors,
            "is_processing": self.is_processing,
            "stt_enabled": STT_ENABLED,
            "stt_engine": STT_ENGINE if STT_ENABLED else "disabled",
            "last_process_time": self.last_process_time,
            "audio_pipeline_healthy": self.audio_pipeline_healthy,
            "audio_callback_count": self.audio_callback_count,
            "audio_callback_errors": self.audio_callback_errors,
            "time_since_last_audio": time_since_audio,
            "audio_callback_rate": audio_callback_rate,
            "last_chunk_size": self.last_chunk_size
        }
