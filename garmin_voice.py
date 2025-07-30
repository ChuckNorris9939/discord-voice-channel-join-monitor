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
import asyncio

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
    """Manages voice channel activity, recording, and speech recognition."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recording_files = []
        self.recording_task = None
        self.stt_task = None
        self.last_trigger_time = {}
        self._last_ok_time = 0.0

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.vc: voice_recv.VoiceRecvClient | None = None

    def start_recording_chunk(self):
        """Starts recording a new 5-minute audio chunk."""
        if not self.vc:
            return

        timestamp = int(time.time())
        filepath = os.path.join(OUTPUT_DIR, f"chunk_{timestamp}.wav")
        self.recording_files.append(filepath)

        # Use a new sink for each file
        sink = voice_recv.WaveSink(filepath)

        # This is a placeholder for the timed filter logic.
        # A proper implementation would use a more robust callback system.
        # For now, we'll rely on the periodic task to handle file rotation.
        self.vc.listen(sink)
        logger.info(f"Recording new chunk to {filepath}")

        # Clean up old files
        while len(self.recording_files) > 3: # Keep last ~15 mins
            old_file = self.recording_files.pop(0)
            if os.path.exists(old_file):
                os.remove(old_file)

    async def recording_loop(self):
        """Main loop to manage continuous recording."""
        while self.vc and self.vc.is_connected():
            self.start_recording_chunk()
            try:
                await asyncio.sleep(300) # 5 minutes
            except asyncio.CancelledError:
                break

    def stt_loop(self):
        """Periodically runs STT on the latest full recording chunk."""
        while self.vc and self.vc.is_connected():
            if len(self.recording_files) > 1:
                # Process the second to last file, as the last one is still being written to.
                filepath = self.recording_files[-2]
                if os.path.exists(filepath):
                    text = self._recognize_audio(filepath)
                    if text:
                        self._handle_triggers(text)
            time.sleep(PROCESS_INTERVAL_S * 5) # Check less frequently

    def _recognize_audio(self, filepath: str) -> str:
        """Perform STT on the given audio file."""
        try:
            with sr.AudioFile(filepath) as source:
                audio = self.recognizer.record(source)
            return self.recognizer.recognize_google(audio, language="de-DE")
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            logger.error(f"Google STT request error for {filepath}: {e}")
            return ""

    def _handle_triggers(self, text: str):
        text = text.lower().strip()
        logger.debug(f"STT: '{text}'")

        now = time.time()
        for trig in sorted(TRIGGERS, key=lambda t: len(t["phrase"]), reverse=True):
            if difflib.SequenceMatcher(None, trig["phrase"], text).ratio() < trig["threshold"]:
                continue

            if trig["name"] == "save" and now - self._last_ok_time > 5:
                continue

            if now - self.last_trigger_time.get(trig["name"], 0) < TRIGGER_COOLDOWN_S:
                continue

            self.last_trigger_time[trig["name"]] = now
            if trig["name"] == "ding":
                self._last_ok_time = now

            if trig["save"]:
                self.save_recording()
            self.play_sound(trig["sound"])
            break

    async def join_channel(self, channel: discord.VoiceChannel):
        if self.vc:
            await self.leave_channel()

        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        self.recording_task = self.bot.loop.create_task(self.recording_loop())
        self.stt_task = threading.Thread(target=self.stt_loop, daemon=True)
        self.stt_task.start()
        logger.info(f"🔊 Joined voice channel '{channel.name}'")

    async def leave_channel(self):
        if self.recording_task:
            self.recording_task.cancel()
            self.recording_task = None

        if self.stt_task:
            # This thread will exit on its own since the vc is disconnected
            self.stt_task = None

        if self.vc:
            # Stop listening will cleanup the current sink
            self.vc.stop()
            await self.vc.disconnect()
            logger.info("🔇 Left voice channel")
            self.vc = None

        # Clean up any remaining recording files
        for f in self.recording_files:
            if os.path.exists(f):
                os.remove(f)
        self.recording_files.clear()

    def save_recording(self):
        """Concatenates recent recording chunks and saves as MP3."""
        if not self.recording_files:
            logger.warning("No recording files to save.")
            return

        # Create a file list for ffmpeg
        concat_list_path = os.path.join(OUTPUT_DIR, "concat_list.txt")
        with open(concat_list_path, "w") as f:
            for filepath in self.recording_files:
                if os.path.exists(filepath):
                    f.write(f"file '{os.path.abspath(filepath)}'\n")

        timestamp = int(time.time())
        mp3_path = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.mp3")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c:a", "libmp3lame", "-b:a", "192k", mp3_path
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"Recording saved: {mp3_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg failed: {e.stderr.decode()}")
        finally:
            if os.path.exists(concat_list_path):
                os.remove(concat_list_path)

    def play_sound(self, filepath: str):
        if self.vc and os.path.isfile(filepath):
            self.vc.play(discord.FFmpegPCMAudio(filepath))
        else:
            logger.warning(f"Sound file '{filepath}' not found or VC is None")
