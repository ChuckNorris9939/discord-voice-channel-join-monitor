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
# Logging setup - now handled by config_loader.py
# --------------------------------------------------
logger = logging.getLogger(__name__)

# Ensure logs directory exists
import os
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, "data", "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

import config_loader as cfg
# ==================================================
# Configurable Speech‑to‑Text backend
# ==================================================
# --------------------------------------------------
# Audio / Recording constants (configurable via environment)
# --------------------------------------------------
SAMPLERATE: Final[int] = int(os.getenv("GARMIN_SAMPLERATE", "48000"))  # Discord standard
CHANNELS: Final[int] = int(os.getenv("GARMIN_CHANNELS", "2"))  # stereo
BYTES_PER_SAMPLE: Final[int] = int(os.getenv("GARMIN_BYTES_PER_SAMPLE", "2"))  # 16‑bit
MAX_BUFFER_SIZE: Final[int] = cfg.GARMIN_RECORD_SECONDS * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE

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
AUDIO_CALLBACK_TIMEOUT: Final[float] = float(os.getenv("GARMIN_AUDIO_CALLBACK_TIMEOUT", "60.0"))  # Max time between audio callbacks (increased to 60s)
MIN_AUDIO_CHUNK_SIZE: Final[int] = int(os.getenv("GARMIN_MIN_AUDIO_CHUNK", "960"))  # More lenient minimum (10ms @ 48kHz stereo)
MAX_AUDIO_CHUNK_SIZE: Final[int] = int(os.getenv("GARMIN_MAX_AUDIO_CHUNK", "19200"))  # More lenient maximum (200ms @ 48kHz stereo)
AUDIO_ERROR_THRESHOLD: Final[int] = int(os.getenv("GARMIN_AUDIO_ERROR_THRESHOLD", "10"))  # Max errors before marking unhealthy

# --------------------------------------------------
# Recording management constants
# --------------------------------------------------
RECORDING_RESTART_DELAY: Final[float] = float(os.getenv("GARMIN_RECORDING_RESTART_DELAY", "1.0"))
MAX_RECORDING_DURATION: Final[int] = int(os.getenv("GARMIN_MAX_RECORDING_DURATION", "3600"))  # 1 hour
MIN_RECORDING_DURATION: Final[int] = int(os.getenv("GARMIN_MIN_RECORDING_DURATION", "600"))  # 10 minutes maximum to save
BUFFER_MONITOR_INTERVAL: Final[float] = float(os.getenv("GARMIN_BUFFER_MONITOR_INTERVAL", "15.0"))
MAX_RECORDING_ERRORS: Final[int] = int(os.getenv("GARMIN_MAX_RECORDING_ERRORS", "5"))

# --------------------------------------------------
# Per-user recording constants
# --------------------------------------------------
USER_BUFFER_MAX_SIZE: Final[int] = int(os.getenv("GARMIN_USER_BUFFER_MAX_SIZE", "48000000"))  # 10 minutes @ 48kHz stereo
USER_BUFFER_CLEANUP_INTERVAL: Final[float] = float(os.getenv("GARMIN_USER_BUFFER_CLEANUP_INTERVAL", "600.0"))  # 10 minutes

# --------------------------------------------------
# Path configuration
# --------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(SCRIPT_DIR, "assets", "sounds")
TEMP_DIR = os.path.join(SCRIPT_DIR, "data", "temp")

# --------------------------------------------------
# Trigger phrase detection
# --------------------------------------------------
SOUND_DING       = os.path.join(SOUNDS_DIR, "garmin_ding.wav")
SOUND_DINGDING  = os.path.join(SOUNDS_DIR, "garmin_dingding.wav")

TRIGGERS = [
    {  # wake word
        "name": "ding",
        "phrase": "okay garmin",
        "threshold": 0.7,
        "sound": SOUND_DING,
        "save": False,
    },
        {  # wake word 2
        "name": "ding2",
        "phrase": "okay garmin video speichern",
        "threshold": 0.7,
        "sound": SOUND_DINGDING,
        "save": True,
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
OUTPUT_DIR: Final[str] = os.path.join(SCRIPT_DIR, "data", "garmin-output")


class GarminVoiceManager:
    """Voice listener that reacts on trigger phrases and plays sounds."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.audio_buffer = bytearray()
        self._buf_lock   = threading.Lock()

        # Per-user audio buffers to prevent stuttering
        self.user_buffers: dict[int, bytearray] = {}  # user_id -> audio buffer
        self.user_buffer_locks: dict[int, threading.Lock] = {}  # user_id -> lock
        self.user_last_activity: dict[int, float] = {}  # user_id -> last activity timestamp
        self._user_buffers_lock = threading.Lock()  # Lock for user_buffers dict operations

        # STT processing queue to prevent conflicts
        self.stt_queue = queue.Queue(maxsize=2)
        self.stt_worker_thread = None
        self.stt_worker_running = False

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True

        # Optional Vosk model
        self.vosk_model = None
        if cfg.STT_ENGINE == "vosk":
            if vosk is None:
                raise RuntimeError("STT_ENGINE='vosk' but 'vosk' package missing.")
            model_path = Path(cfg.VOSK_MODEL_PATH)
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
        self.user_buffer_cleanup_task: asyncio.Task | None = None

        # Audio pipeline health monitoring
        self.last_audio_callback_time: float = 0.0
        self.audio_callback_count: int = 0
        self.audio_callback_errors: int = 0
        self.last_chunk_size: int = 0
        self.audio_pipeline_healthy: bool = True

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        self.vc: voice_recv.VoiceRecvClient | None = None

    # ------------------------- Discord voice callbacks -------------------------
    def callback(self, user: discord.User | None, data: voice_recv.VoiceData):
        """Callback for incoming audio data with per-user buffer management and health monitoring."""
        now = time.time()
        
        # Audio pipeline health monitoring
        self.audio_callback_count += 1
        chunk_size = len(data.pcm)
        self.last_chunk_size = chunk_size
        
        # More lenient audio chunk size validation for Discord's variable bitrate
        if chunk_size < MIN_AUDIO_CHUNK_SIZE:
            self.audio_callback_errors += 1
            logger.debug(f"Small audio chunk: {chunk_size} bytes (min: {MIN_AUDIO_CHUNK_SIZE})")
        elif chunk_size > MAX_AUDIO_CHUNK_SIZE:
            self.audio_callback_errors += 1
            logger.debug(f"Large audio chunk: {chunk_size} bytes (max: {MAX_AUDIO_CHUNK_SIZE})")
        
        # Only mark unhealthy if we have too many errors
        if self.audio_callback_errors >= AUDIO_ERROR_THRESHOLD:
            self.audio_pipeline_healthy = False
            logger.warning(f"Audio pipeline marked unhealthy due to {self.audio_callback_errors} errors")
        
        # Check for audio callback timeouts (indicates stuttering)
        if self.last_audio_callback_time > 0:
            time_since_last = now - self.last_audio_callback_time
            if time_since_last > AUDIO_CALLBACK_TIMEOUT:
                self.audio_callback_errors += 1
                logger.warning(f"Audio callback timeout: {time_since_last:.2f}s since last callback")
                if self.audio_callback_errors >= AUDIO_ERROR_THRESHOLD:
                    self.audio_pipeline_healthy = False
        
        self.last_audio_callback_time = now
        
        # Handle per-user audio buffers
        if user is not None:
            self._handle_user_audio(user.id, data.pcm, now)
            logger.debug(f"Audio callback: user {user.id} ({user.name}), chunk size: {len(data.pcm)} bytes")
        else:
            # If no user is provided, use a special "unknown" user ID
            # This can happen when Discord doesn't provide user information
            unknown_user_id = 0  # Special ID for unknown users
            self._handle_user_audio(unknown_user_id, data.pcm, now)
            logger.debug(f"Audio callback: unknown user (ID: {unknown_user_id}), chunk size: {len(data.pcm)} bytes")
        
        # Append to combined buffer with validation (for STT processing)
        try:
            self._append_to_buffer(data.pcm)
        except Exception as e:
            self.audio_callback_errors += 1
            logger.error(f"Error appending audio to buffer: {e}")
            if self.audio_callback_errors >= AUDIO_ERROR_THRESHOLD:
                self.audio_pipeline_healthy = False
            return
        
        # Skip STT processing if disabled
        if not cfg.STT_ENABLED:
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

    def _handle_user_audio(self, user_id: int, audio_data: bytes, timestamp: float):
        """Handle audio data for a specific user with buffer management."""
        try:
            with self._user_buffers_lock:
                # Initialize user buffer if not exists
                if user_id not in self.user_buffers:
                    self.user_buffers[user_id] = bytearray()
                    self.user_buffer_locks[user_id] = threading.Lock()
                    logger.info(f"Created audio buffer for user {user_id}")
                
                # Update last activity
                self.user_last_activity[user_id] = timestamp
            
            # Append audio data to user's buffer
            user_lock = self.user_buffer_locks[user_id]
            with user_lock:
                user_buffer = self.user_buffers[user_id]
                old_size = len(user_buffer)
                user_buffer.extend(audio_data)
                new_size = len(user_buffer)
                
                # Log buffer growth periodically (every 5MB instead of 1MB to reduce spam)
                if new_size % 5000000 < len(audio_data):  # Log every ~5MB
                    user_name = "unknown" if user_id == 0 else str(user_id)
                    duration = new_size / SAMPLERATE / CHANNELS / BYTES_PER_SAMPLE
                    logger.info(f"User {user_name} buffer: {old_size} -> {new_size} bytes (+{len(audio_data)}), duration: {duration:.1f}s")
                
                # Manage buffer size to prevent memory issues
                if len(user_buffer) > USER_BUFFER_MAX_SIZE:
                    # Keep only the most recent data (sliding window)
                    excess = len(user_buffer) - USER_BUFFER_MAX_SIZE
                    del user_buffer[:excess]
                    user_name = "unknown" if user_id == 0 else str(user_id)
                    logger.info(f"User {user_name} buffer overflow prevented: removed {excess} bytes")
                    
        except Exception as e:
            logger.error(f"Error handling audio for user {user_id}: {e}")
            # Don't let errors in user audio handling break the entire system

    def _append_to_buffer(self, chunk: bytes):
        """Append audio data to combined buffer with improved overflow management."""
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

    def _cleanup_inactive_users(self):
        """Remove audio buffers for users who haven't been active recently."""
        now = time.time()
        inactive_users = []
        
        with self._user_buffers_lock:
            total_users = len(self.user_buffers)
            total_buffer_size = sum(len(buf) for buf in self.user_buffers.values())
            
            for user_id, last_activity in self.user_last_activity.items():
                time_since_activity = now - last_activity
                if time_since_activity > USER_BUFFER_CLEANUP_INTERVAL:
                    inactive_users.append(user_id)
                    user_name = "unknown" if user_id == 0 else str(user_id)
                    buffer_size = len(self.user_buffers.get(user_id, bytearray()))
                    logger.info(f"Marking user {user_name} as inactive (inactive for {time_since_activity:.1f}s, buffer: {buffer_size} bytes)")
            
            # Remove inactive users
            for user_id in inactive_users:
                if user_id in self.user_buffers:
                    buffer_size = len(self.user_buffers[user_id])
                    del self.user_buffers[user_id]
                if user_id in self.user_buffer_locks:
                    del self.user_buffer_locks[user_id]
                if user_id in self.user_last_activity:
                    del self.user_last_activity[user_id]
                user_name = "unknown" if user_id == 0 else str(user_id)
                logger.info(f"Cleaned up inactive user buffer: {user_name} (was {buffer_size} bytes)")
            
            if inactive_users:
                remaining_users = len(self.user_buffers)
                remaining_buffer_size = sum(len(buf) for buf in self.user_buffers.values())
                logger.info(f"After cleanup: {remaining_users}/{total_users} users remaining, {remaining_buffer_size}/{total_buffer_size} bytes remaining")

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
            if cfg.STT_ENGINE == "vosk":
                import audioop, json
                mono = audioop.tomono(window, BYTES_PER_SAMPLE, 0.5, 0.5)
                pcm16k, _ = audioop.ratecv(mono, BYTES_PER_SAMPLE, 1, SAMPLERATE, 16_000, None)
                rec = vosk.KaldiRecognizer(self.vosk_model, 16_000)
                rec.AcceptWaveform(pcm16k)
                text = json.loads(rec.Result()).get("text", "")
            else:  # Google
                import concurrent.futures, socket
                socket.setdefaulttimeout(8)
                tmp_path = os.path.join(TEMP_DIR, f"temp_{int(time.time()*1000)}.wav")
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
                # Only log STT output if enabled in settings
                if cfg.GARMIN_STT_OUTPUT_ENABLED:
                    logger.info("STT[%s]: '%s'", cfg.STT_ENGINE, text)
            else:
                # Log empty results at DEBUG level to avoid spam (only if STT output is enabled)
                if cfg.GARMIN_STT_OUTPUT_ENABLED:
                    logger.debug("STT[%s]: (no text detected)", cfg.STT_ENGINE)
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
        """Save current recording by combining per-user audio streams and restart with a delay."""
        try:
            # Stop current recording monitoring
            if self.buffer_monitor_task and not self.buffer_monitor_task.done():
                self.buffer_monitor_task.cancel()
                logger.debug("Cancelled buffer monitor task for save operation")
            
            # Always save, but limit to last 10 minutes of audio
            recording_duration = time.time() - self.recording_start_time
            logger.info(f"Saving recording with duration: {recording_duration:.1f}s (will limit to last {MIN_RECORDING_DURATION}s if longer)")
            
            # Log current buffer status before combining
            with self._user_buffers_lock:
                total_users = len(self.user_buffers)
                total_buffer_size = sum(len(buf) for buf in self.user_buffers.values())
                logger.info(f"Before combining: {total_users} users, total buffer size: {total_buffer_size} bytes")
            
            filename = f"recording_{time.strftime('%d.%m.%Y_%H-%M', time.localtime())}.wav"
            path = os.path.join(OUTPUT_DIR, filename)
            
            # Combine per-user audio streams
            combined_audio = self._combine_user_audio_streams()
            
            if combined_audio:
                with wave.open(path, "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(BYTES_PER_SAMPLE)
                    wf.setframerate(SAMPLERATE)
                    wf.writeframes(combined_audio)
                
                # Calculate actual saved duration
                saved_duration = len(combined_audio) / SAMPLERATE / CHANNELS / BYTES_PER_SAMPLE
                logger.info("Recording saved: %s (requested: %.1fs, actual: %.1fs, users: %d)", 
                          path, recording_duration, saved_duration, len(self.user_buffers))
            else:
                logger.warning("No audio data available for saving")
            
            # Clear all buffers and restart recording
            self._clear_all_buffers(clear_user_buffers=True)  # Clear user buffers after saving
            
            # Schedule restart of recording
            if self.vc and self.vc.is_connected():
                # Schedule the restart coroutine on the bot's event loop
                if hasattr(self.bot, 'loop') and self.bot.loop and self.bot.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self._restart_recording_after_save(), self.bot.loop)
                else:
                    logger.warning("No event loop available to restart recording after save")
                
        except Exception as e:
            logger.error("Error during save_recording: %s", e)
            self.recording_errors += 1

    def _combine_user_audio_streams(self) -> bytes:
        """Combine all user audio streams into a single synchronized audio file."""
        try:
            with self._user_buffers_lock:
                logger.info(f"Combining user audio streams. Total user buffers: {len(self.user_buffers)}")
                
                # Log detailed buffer information
                total_buffer_size = 0
                for user_id, buffer in self.user_buffers.items():
                    buffer_size = len(buffer)
                    total_buffer_size += buffer_size
                    user_name = "unknown" if user_id == 0 else str(user_id)
                    logger.info(f"User {user_name} buffer size: {buffer_size} bytes ({buffer_size/SAMPLERATE/CHANNELS/BYTES_PER_SAMPLE:.1f}s)")
                
                if not self.user_buffers:
                    logger.warning("No user audio buffers available for combining")
                    return b""
                
                if total_buffer_size == 0:
                    logger.warning("All user buffers are empty (total size: 0 bytes)")
                    return b""
                
                # Get all user buffers with their locks and limit to last 10 minutes
                user_data = {}
                max_audio_bytes = MIN_RECORDING_DURATION * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE
                
                for user_id, buffer in self.user_buffers.items():
                    user_lock = self.user_buffer_locks.get(user_id)
                    if user_lock:
                        with user_lock:
                            buffer_size = len(buffer)
                            if buffer_size == 0:
                                logger.warning(f"User {user_id} buffer is empty, skipping")
                                continue
                            
                            # Take only the last 10 minutes of audio data
                            if buffer_size > max_audio_bytes:
                                audio_data = bytes(buffer[-max_audio_bytes:])
                                user_name = "unknown" if user_id == 0 else str(user_id)
                                logger.info(f"User {user_name}: truncated from {buffer_size} to {len(audio_data)} bytes (last 10 minutes)")
                            else:
                                audio_data = bytes(buffer)
                                user_name = "unknown" if user_id == 0 else str(user_id)
                                logger.info(f"User {user_name}: using all {len(audio_data)} bytes")
                            user_data[user_id] = audio_data
                
                if not user_data:
                    logger.warning("No valid user audio data found after processing")
                    return b""
                
                # Find the shortest buffer length to synchronize all streams
                min_length = min(len(data) for data in user_data.values())
                if min_length == 0:
                    logger.warning("All user buffers are empty after processing")
                    return b""
                
                logger.info(f"Combining {len(user_data)} user streams, min length: {min_length} bytes ({min_length/SAMPLERATE/CHANNELS/BYTES_PER_SAMPLE:.1f}s)")
                
                # Try to use FFmpeg for better audio mixing if available
                if self._try_ffmpeg_mixing(user_data, min_length):
                    return self._get_ffmpeg_mixed_audio()
                else:
                    # Fallback to simple byte-level mixing
                    return self._simple_audio_mixing(user_data, min_length)
                
        except Exception as e:
            logger.error(f"Error combining user audio streams: {e}")
            return b""

    def _try_ffmpeg_mixing(self, user_data: dict, min_length: int) -> bool:
        """Try to use FFmpeg for audio mixing. Returns True if successful."""
        try:
            import subprocess
            import tempfile
            
            # Check if FFmpeg is available
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                logger.debug("FFmpeg not available, using simple mixing")
                return False
            
            # Create temporary files for each user's audio
            temp_files = []
            try:
                for user_id, audio_data in user_data.items():
                    # Create temporary WAV file
                    temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                    temp_files.append(temp_file.name)
                    
                    with wave.open(temp_file.name, "wb") as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(BYTES_PER_SAMPLE)
                        wf.setframerate(SAMPLERATE)
                        wf.writeframes(audio_data)
                    
                    temp_file.close()
                
                # Create output temporary file
                output_temp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                output_temp.close()
                
                # Build FFmpeg command for mixing
                input_args = []
                for temp_file in temp_files:
                    input_args.extend(['-i', temp_file])
                
                # Use amix filter for mixing
                filter_complex = f"amix=inputs={len(temp_files)}:duration=longest"
                
                cmd = ['ffmpeg'] + input_args + [
                    '-filter_complex', filter_complex,
                    '-ar', str(SAMPLERATE),
                    '-ac', str(CHANNELS),
                    '-y',  # Overwrite output file
                    output_temp.name
                ]
                
                # Run FFmpeg
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0 and os.path.exists(output_temp.name):
                    # Read the mixed audio file
                    with open(output_temp.name, 'rb') as f:
                        self._ffmpeg_mixed_audio = f.read()
                    
                    logger.info(f"Successfully mixed {len(user_data)} user streams using FFmpeg")
                    return True
                else:
                    logger.warning(f"FFmpeg mixing failed: {result.stderr}")
                    return False
                    
            finally:
                # Clean up temporary files
                for temp_file in temp_files:
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
                try:
                    os.unlink(output_temp.name)
                except:
                    pass
                    
        except Exception as e:
            logger.debug(f"FFmpeg mixing failed: {e}")
            return False

    def _get_ffmpeg_mixed_audio(self) -> bytes:
        """Get the FFmpeg mixed audio data."""
        return getattr(self, '_ffmpeg_mixed_audio', b'')

    def _simple_audio_mixing(self, user_data: dict, min_length: int) -> bytes:
        """Simple byte-level audio mixing as fallback."""
        try:
            # Combine audio streams by mixing them together
            combined = bytearray(min_length)
            
            for user_id, audio_data in user_data.items():
                # Mix audio by averaging the samples
                for i in range(0, min_length, BYTES_PER_SAMPLE * CHANNELS):
                    if i + BYTES_PER_SAMPLE * CHANNELS <= len(audio_data):
                        # For stereo, mix left and right channels separately
                        for channel in range(CHANNELS):
                            for sample_byte in range(BYTES_PER_SAMPLE):
                                byte_offset = i + channel * BYTES_PER_SAMPLE + sample_byte
                                if byte_offset < len(audio_data):
                                    # Convert to integer, mix, and convert back
                                    current_val = audio_data[byte_offset]
                                    existing_val = combined[byte_offset] if byte_offset < len(combined) else 0
                                    # Simple averaging (can be improved with proper audio mixing)
                                    mixed_val = (current_val + existing_val) // 2
                                    if byte_offset < len(combined):
                                        combined[byte_offset] = mixed_val
            
            logger.info(f"Successfully combined {len(user_data)} user audio streams using simple mixing")
            return bytes(combined)
            
        except Exception as e:
            logger.error(f"Error in simple audio mixing: {e}")
            return b""

    def _clear_all_buffers(self, clear_user_buffers: bool = True):
        """Clear audio buffers. Optionally preserve user buffers."""
        # Clear combined buffer
        with self._buf_lock:
            self.audio_buffer.clear()
        
        if clear_user_buffers:
            # Clear all user buffers
            with self._user_buffers_lock:
                for user_id in list(self.user_buffers.keys()):
                    user_lock = self.user_buffer_locks.get(user_id)
                    if user_lock:
                        with user_lock:
                            self.user_buffers[user_id].clear()
            logger.debug("Cleared all audio buffers (including user buffers)")
        else:
            logger.debug("Cleared combined buffer only (user buffers preserved)")

    def play_sound(self, filepath: str):
        if self.vc and os.path.isfile(filepath):
            self.vc.play(discord.FFmpegPCMAudio(filepath))
        else:
            logger.warning("Sound file '%s' not found or VC is None", filepath)

    # ---------------------- Voice connection helpers ---------------------------
    async def join_channel(self, channel: discord.VoiceChannel):
        """Connect to a voice channel and start listening. Disconnect first if already connected."""
        try:
            # Ensure clean disconnection if already connected
            if self.is_connected():
                await self.leave_channel()
                # Small delay to ensure clean disconnection
                await asyncio.sleep(0.5)
            
            # Connect to the voice channel
            self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
            self.vc.listen(voice_recv.BasicSink(self.callback))
            
            # Only start STT worker if STT is enabled
            if cfg.STT_ENABLED:
                self._start_stt_worker()
                logger.info("🔊 Joined voice channel '%s' (STT engine: %s)", channel.name, cfg.STT_ENGINE)
            else:
                logger.info("🔊 Joined voice channel '%s' (STT disabled)", channel.name)
            
            await self._start_recording()
            
        except discord.ClientException as e:
            logger.error(f"Client exception while joining voice channel '{channel.name}': {e}")
            # Ensure cleanup on client exception (like "Already connected")
            if self.vc:
                try:
                    await self.vc.disconnect()
                except:
                    pass
            self.vc = None
            raise
        except Exception as e:
            logger.error(f"Failed to join voice channel '{channel.name}': {e}")
            # Clean up on error
            if self.vc:
                try:
                    await self.vc.disconnect()
                except:
                    pass
                self.vc = None
            raise

    def is_connected(self) -> bool:
        """Check if the voice client is properly connected."""
        if not self.vc:
            return False
        
        try:
            # Check if the voice client has the required attributes and is connected
            return (hasattr(self.vc, 'is_connected') and 
                   self.vc.is_connected() and 
                   hasattr(self.vc, 'ws') and 
                   self.vc.ws and 
                   hasattr(self.vc.ws, 'close'))
        except Exception:
            return False

    async def leave_channel(self):
        """Disconnect from the current voice connection (if any)."""
        try:
            await self._stop_recording()
            self._stop_stt_worker()
            
            # Clear all user buffers only when leaving channel
            self._clear_all_buffers(clear_user_buffers=True)
            with self._user_buffers_lock:
                self.user_buffers.clear()
                self.user_buffer_locks.clear()
                self.user_last_activity.clear()
            
            if self.vc:
                # Check if the voice client is in a valid state before disconnecting
                if self.is_connected():
                    try:
                        await self.vc.disconnect()
                        logger.info("🔇 Left voice channel")
                    except Exception as e:
                        logger.warning(f"Error during voice disconnect: {e}")
                else:
                    logger.warning("Voice client WebSocket in invalid state, forcing cleanup")
                
                self.vc = None
        except Exception as e:
            logger.error(f"Error in leave_channel: {e}")
            # Ensure vc is set to None even on error
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
        
        # Start user buffer cleanup task
        if self.user_buffer_cleanup_task and not self.user_buffer_cleanup_task.done():
            self.user_buffer_cleanup_task.cancel()
        self.user_buffer_cleanup_task = asyncio.create_task(self._monitor_user_buffers())

    async def _stop_recording(self):
        """Stop recording monitoring."""
        if self.buffer_monitor_task and not self.buffer_monitor_task.done():
            self.buffer_monitor_task.cancel()
            try:
                await self.buffer_monitor_task
            except asyncio.CancelledError:
                pass
            logger.debug("Recording monitoring stopped")
        
        if self.user_buffer_cleanup_task and not self.user_buffer_cleanup_task.done():
            self.user_buffer_cleanup_task.cancel()
            try:
                await self.user_buffer_cleanup_task
            except asyncio.CancelledError:
                pass
            logger.debug("User buffer monitoring stopped")

    async def _restart_recording_after_save(self):
        """Restart recording after a save operation with delay."""
        await asyncio.sleep(RECORDING_RESTART_DELAY)
        if self.is_connected():
            await self._start_recording()
            logger.debug("Recording restarted after save operation")

    async def _monitor_recording_buffer(self):
        """Monitor recording health and restart if needed."""
        while True:
            try:
                await asyncio.sleep(BUFFER_MONITOR_INTERVAL)
                
                # Skip health checks if not connected to a voice channel
                if not self.is_connected():
                    logger.debug("Bot not connected to voice channel - skipping health checks")
                    continue
                
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
                
                # Check audio pipeline health - only restart if errors persist
                elif not self.audio_pipeline_healthy and self.audio_callback_errors >= AUDIO_ERROR_THRESHOLD * 2:
                    needs_restart = True
                    restart_reason = f"audio pipeline persistently unhealthy (errors: {self.audio_callback_errors})"
                
                # Check for audio callback timeouts (indicates stuttering) - only if we have no audio at all
                elif (self.last_audio_callback_time > 0 and 
                      current_time - self.last_audio_callback_time > AUDIO_CALLBACK_TIMEOUT and
                      buffer_size == 0):  # Only restart if buffer is completely empty
                    needs_restart = True
                    restart_reason = f"audio callback timeout with empty buffer ({current_time - self.last_audio_callback_time:.1f}s)"
                
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
                    logger.info(f"Recording health OK - duration: {recording_duration:.1f}s, buffer: {buffer_size} bytes, "
                               f"errors: {self.recording_errors}, audio: {audio_status}, "
                               f"audio_errors: {self.audio_callback_errors}, time_since_audio: {time_since_audio:.1f}s")
                    
                    # Auto-recover audio pipeline if it's been healthy for a while
                    if not self.audio_pipeline_healthy and self.audio_callback_errors < AUDIO_ERROR_THRESHOLD:
                        self.audio_pipeline_healthy = True
                        logger.info("Audio pipeline auto-recovered - marking as healthy")
                    
            except asyncio.CancelledError:
                logger.debug("Buffer monitoring task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in buffer monitoring: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def _monitor_user_buffers(self):
        """Monitor and cleanup inactive user buffers."""
        while True:
            try:
                await asyncio.sleep(USER_BUFFER_CLEANUP_INTERVAL)
                
                # Skip user buffer monitoring if not connected to a voice channel
                if not self.is_connected():
                    logger.debug("Bot not connected to voice channel - skipping user buffer monitoring")
                    continue
                
                self._cleanup_inactive_users()
                
                # Log user buffer status
                with self._user_buffers_lock:
                    active_users = len(self.user_buffers)
                    total_buffer_size = sum(len(buf) for buf in self.user_buffers.values())
                
                logger.debug(f"User buffer status: {active_users} active users, {total_buffer_size} total bytes")
                
            except asyncio.CancelledError:
                logger.debug("User buffer monitoring task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in user buffer monitoring: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def _restart_recording(self):
        """Restart recording by clearing state and buffer."""
        logger.info("Restarting recording system...")
        
        # Stop STT worker if running
        self._stop_stt_worker()
        
        # Reset recording state
        self.recording_start_time = time.time()
        self.last_buffer_check = time.time()
        self.recording_errors = 0
        
        # Reset audio pipeline health
        self.audio_pipeline_healthy = True
        self.audio_callback_errors = 0
        self.last_audio_callback_time = 0.0
        
        # Clear only the combined buffer (for STT), but KEEP user buffers
        with self._buf_lock:
            self.audio_buffer.clear()
        
        # Update user activity timestamps to prevent premature cleanup
        now = time.time()
        with self._user_buffers_lock:
            for user_id in self.user_last_activity:
                self.user_last_activity[user_id] = now
        
        # Restart STT worker if STT is enabled
        if cfg.STT_ENABLED:
            self._start_stt_worker()
            logger.info("STT worker restarted after recording restart")
        
        logger.info("Recording system restarted (user buffers preserved)")

    def get_recording_health(self) -> dict:
        """Get current recording system health status."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time
        
        with self._buf_lock:
            buffer_size = len(self.audio_buffer)
        
        # Calculate audio pipeline metrics
        time_since_audio = current_time - self.last_audio_callback_time if self.last_audio_callback_time > 0 else 0
        audio_callback_rate = self.audio_callback_count / max(recording_duration, 1) if recording_duration > 0 else 0
        
        # Get user buffer information
        with self._user_buffers_lock:
            active_users = len(self.user_buffers)
            total_user_buffer_size = sum(len(buf) for buf in self.user_buffers.values())
            user_buffer_info = {}
            for user_id, buffer in self.user_buffers.items():
                last_activity = self.user_last_activity.get(user_id, 0)
                time_since_activity = current_time - last_activity if last_activity > 0 else 0
                user_buffer_info[user_id] = {
                    "buffer_size": len(buffer),
                    "time_since_activity": time_since_activity
                }
        
        return {
            "connected": self.is_connected(),
            "recording_duration": recording_duration,
            "max_recording_duration": MIN_RECORDING_DURATION,  # Changed: now represents max duration to save
            "buffer_size": buffer_size,
            "recording_errors": self.recording_errors,
            "max_errors": self.max_errors,
            "is_processing": self.is_processing,
            "stt_enabled": cfg.STT_ENABLED,
            "stt_engine": cfg.STT_ENGINE if cfg.STT_ENABLED else "disabled",
            "last_process_time": self.last_process_time,
            "audio_pipeline_healthy": self.audio_pipeline_healthy,
            "audio_callback_count": self.audio_callback_count,
            "audio_callback_errors": self.audio_callback_errors,
            "time_since_last_audio": time_since_audio,
            "audio_callback_rate": audio_callback_rate,
            "last_chunk_size": self.last_chunk_size,
            "active_users": active_users,
            "total_user_buffer_size": total_user_buffer_size,
            "user_buffers": user_buffer_info
        }
