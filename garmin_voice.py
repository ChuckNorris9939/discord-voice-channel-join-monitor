import os
import sys
import time
import wave
import threading
import difflib
import logging
import subprocess
import queue
from typing import Final
from pathlib import Path

import discord
import speech_recognition as sr
from discord.ext import voice_recv

# --------------------------------------------------
# Custom Buffering Sink
# --------------------------------------------------
class BufferingSink(voice_recv.AudioSink):
    def __init__(self):
        super().__init__()
        self.buffer = bytearray()
        self.lock = threading.Lock()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData):
        with self.lock:
            self.buffer.extend(data.pcm)

    def get_and_swap_buffer(self) -> bytearray:
        with self.lock:
            old_buffer = self.buffer
            self.buffer = bytearray()
            return old_buffer

    def get_buffer_view(self) -> memoryview:
        """Returns a memoryview of the buffer for reading without locking."""
        return memoryview(self.buffer)

    def cleanup(self):
        pass

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
STT_ENGINE: Final[str] = os.getenv("STT_ENGINE", "google").lower()
VOSK_MODEL_PATH: Final[str] = os.getenv("VOSK_MODEL_PATH", "vosk-model-de")

# --------------------------------------------------
# Audio / Recording constants
# --------------------------------------------------
RECORD_SECONDS: Final[int] = 10 * 60     # keep last 10 min
SAMPLERATE:     Final[int] = 48_000      # Discord standard
CHANNELS:       Final[int] = 2           # stereo
BYTES_PER_SAMPLE: Final[int] = 2        # 16‑bit
MAX_BUFFER_SIZE: Final[int] = RECORD_SECONDS * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE

FRAMES_PER_BUFFER: Final[int] = 960  # 20 ms @48 kHz
CHUNK_SIZE:        Final[int] = FRAMES_PER_BUFFER * CHANNELS * BYTES_PER_SAMPLE

# --------------------------------------------------
# Trigger phrase detection
# --------------------------------------------------
SOUND_DING      = "sounds/garmin_ding.wav"
SOUND_DINGDING  = "sounds/garmin_dingding.wav"

TRIGGERS = [
    {  # wake word
        "name": "ding",
        "phrase": "okay garmin",
        "threshold": 0.65,
        "sound": SOUND_DING,
        "save": False,
    },
    {  # follow‑up within 5 s after wake word
        "name": "save",
        "phrase": "video speichern",
        "threshold": 0.65,
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
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.buffer_sink = BufferingSink()

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
        self._last_ok_time: float = 0.0
        self._last_stt_text: str = ""
        self._processing_thread: threading.Thread | None = None

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.vc: voice_recv.VoiceRecvClient | None = None

    def _stt_worker(self):
        """Periodically runs STT on the audio buffer."""
        while self.vc and self.vc.is_connected():
            if self.is_processing:
                time.sleep(PROCESS_INTERVAL_S)
                continue

            try:
                self.is_processing = True
                self._process_audio_data()
            except Exception as e:
                logger.error("Error in STT worker: %s", e, exc_info=True)
            finally:
                self.is_processing = False
                time.sleep(PROCESS_INTERVAL_S)

    def _process_audio_data(self):
        with self.buffer_sink.lock:
            # Trim the buffer to the max size
            if len(self.buffer_sink.buffer) > MAX_BUFFER_SIZE:
                del self.buffer_sink.buffer[:len(self.buffer_sink.buffer) - MAX_BUFFER_SIZE]

            buf_view = self.buffer_sink.get_buffer_view()
            if len(buf_view) < WINDOW_BYTES_MIN:
                return

            window = buf_view[-min(len(buf_view), WINDOW_BYTES_MAX):]
            buf_copy = bytes(window)

        # --- Speech-to-Text ---
        text = self._recognize_audio(buf_copy)
        if not text:
            return

        # --- Trigger Detection ---
        self._handle_triggers(text)

    def _recognize_audio(self, pcm_data: bytes) -> str:
        """Perform STT on the given PCM data."""
        if STT_ENGINE == "vosk":
            # (Vosk implementation remains the same)
            ...
        else:  # Google
            tmp_path = os.path.join(OUTPUT_DIR, f"temp_{int(time.time()*1000)}.wav")
            try:
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(BYTES_PER_SAMPLE)
                    wf.setframerate(SAMPLERATE)
                    wf.writeframes(pcm_data)

                with sr.AudioFile(tmp_path) as source:
                    audio = self.recognizer.record(source)

                return self.recognizer.recognize_google(audio, language="de-DE")
            except sr.UnknownValueError:
                return ""
            except sr.RequestError as e:
                logger.error("Google STT request error: %s", e)
                return ""
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        return "" # Fallback for other engines or errors

    def _handle_triggers(self, text: str):
        text = text.lower().strip()
        if text == self._last_stt_text or not text:
            return

        self._last_stt_text = text
        logger.debug("STT[%s]: '%s'", STT_ENGINE, text)

        now = time.time()
        for trig in sorted(TRIGGERS, key=lambda t: len(t["phrase"]), reverse=True):
            if difflib.SequenceMatcher(None, trig["phrase"], text).ratio() < trig["threshold"]:
                continue

            if trig["name"] == "save" and now - self._last_ok_time > 5:
                continue

            if now - self.last_trigger_time[trig["name"]] < TRIGGER_COOLDOWN_S:
                continue

            self.last_trigger_time[trig["name"]] = now
            if trig["name"] == "ding":
                self._last_ok_time = now

            if trig["save"]:
                self.save_recording()
            self.play_sound(trig["sound"])
            break

    def save_recording(self):
        """Saves the entire buffer to an MP3 file."""
        pcm_data = bytes(self.buffer_sink.get_and_swap_buffer())
        timestamp = int(time.time())
        mp3_path = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.mp3")
        temp_wav_path = os.path.join(OUTPUT_DIR, f"temp_full_{timestamp}.wav")

        try:
            with wave.open(temp_wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(BYTES_PER_SAMPLE)
                wf.setframerate(SAMPLERATE)
                wf.writeframes(pcm_data)

            cmd = [
                "ffmpeg", "-y", "-i", temp_wav_path,
                "-codec:a", "libmp3lame", "-b:a", "192k", mp3_path
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info("Recording saved: %s", mp3_path)
        except Exception as e:
            logger.error("Failed to save recording: %s", e)
        finally:
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)

    def play_sound(self, filepath: str):
        if self.vc and os.path.isfile(filepath):
            self.vc.play(discord.FFmpegPCMAudio(filepath))
        else:
            logger.warning("Sound file '%s' not found or VC is None", filepath)

    async def join_channel(self, channel: discord.VoiceChannel):
        if self.vc:
            await self.leave_channel()
        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        self.vc.listen(self.buffer_sink)
        self._processing_thread = threading.Thread(target=self._stt_worker, daemon=True)
        self._processing_thread.start()
        logger.info("🔊 Joined voice channel '%s' (STT engine: %s)", channel.name, STT_ENGINE)

    async def leave_channel(self):
        if self.vc:
            await self.vc.disconnect()
            logger.info("🔇 Left voice channel")
            self.vc = None
            if self._processing_thread:
                self._processing_thread.join(timeout=1)
                self._processing_thread = None
