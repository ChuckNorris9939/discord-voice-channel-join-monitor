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
# Patch BasicSink backlog so no frames are dropped
# --------------------------------------------------
class BigSink(voice_recv.BasicSink):
    """Same as BasicSink but with a much larger internal backlog so that
    callback latency never drops frames (default = 512)."""
    MAX_SIZE = 4096

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
        self.audio_buffer = bytearray()
        self._buf_lock   = threading.Lock()
        # thread‑safe PCM hand‑off
        # unlimited queue to prevent frame drops
        self.pcm_queue: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._buffer_worker, daemon=True).start()

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

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.vc: voice_recv.VoiceRecvClient | None = None

    # ------------------------- Discord voice callbacks -------------------------
    def callback(self, user: discord.User | None, data: voice_recv.VoiceData):
        """Called by discord‑voice‑recv for every PCM frame (20 ms). Put into
        queue so the audio thread never blocks."""
        self.pcm_queue.put(data.pcm)  # block briefly if backlog
        now = time.time()
        if not self.is_processing and (now - self.last_process_time >= PROCESS_INTERVAL_S):
            threading.Thread(target=self._process_audio_data, daemon=True).start()

    def _buffer_worker(self):
        """Continuously move PCM frames from queue into the bytearray buffer
        without resizing it while other threads hold a view."""
        while True:
            chunk = self.pcm_queue.get()
            with self._buf_lock:
                self.audio_buffer.extend(chunk)
                if len(self.audio_buffer) > MAX_BUFFER_SIZE:
                    del self.audio_buffer[:len(self.audio_buffer) - MAX_BUFFER_SIZE]
# ------------------------------ Speech‑rec thread ---------------------------
    def _process_audio_data(self):
        self.is_processing = True
        self.last_process_time = time.time()
        tmp_path = None
        try:
            with self._buf_lock:
                buf_copy = bytes(self.audio_buffer)
            if len(buf_copy) < WINDOW_BYTES_MIN:
                return
            window = buf_copy[-min(len(buf_copy), WINDOW_BYTES_MAX):]

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
        finally:
            if tmp_path and os.path.isfile(tmp_path):
                os.remove(tmp_path)
            self.is_processing = False

    # ------------------------------ Helpers ------------------------------------
    def save_recording(self):
        """Encode the current in-memory PCM buffer to MP3 using ffmpeg."""
        timestamp = int(time.time())
        mp3_path = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.mp3")
        temp_wav_path = os.path.join(OUTPUT_DIR, f"temp_full_{timestamp}.wav")

        with self._buf_lock:
            saved_length = len(self.audio_buffer)
            pcm_data = bytes(self.audio_buffer)

        # Write the entire buffer to a temporary WAV file
        try:
            with wave.open(temp_wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(BYTES_PER_SAMPLE)
                wf.setframerate(SAMPLERATE)
                wf.writeframes(pcm_data)
        except Exception as e:
            logger.error("Failed to write temporary WAV file: %s", e)
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)
            return

        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-y",
            "-i", temp_wav_path,
            "-codec:a", "libmp3lame",
            "-b:a", "192k",
            mp3_path,
        ]
        try:
            subprocess.run(cmd, check=True)
            logger.info("Recording saved: %s", mp3_path)
            # Remove only the saved portion from the buffer
            with self._buf_lock:
                del self.audio_buffer[:saved_length]
        except subprocess.CalledProcessError as e:
            logger.error("ffmpeg failed: %s", e)
        finally:
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)

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
        self.vc.listen(BigSink(self.callback))
        logger.info("🔊 Joined voice channel '%s' (STT engine: %s)", channel.name, STT_ENGINE)

    async def leave_channel(self):
        """Disconnect from the current voice connection (if any)."""
        if self.vc:
            await self.vc.disconnect()
            logger.info("🔇 Left voice channel")
            self.vc = None
