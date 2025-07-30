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
from discord.ext.audiorec import NativeVoiceClient

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
    """Manages voice channel activity using discord.ext.audiorec."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.vc: NativeVoiceClient | None = None

        os.makedirs(OUTPUT_DIR, exist_ok=True)

    async def join_channel(self, channel: discord.VoiceChannel):
        if self.vc:
            await self.leave_channel()

        self.vc = await channel.connect(cls=NativeVoiceClient)
        self.vc.record(lambda e: logger.error(f"Error in recording callback: {e}"))
        logger.info(f"🔊 Joined and started recording in '{channel.name}'")

    async def leave_channel(self):
        if self.vc:
            if self.vc.is_recording():
                await self.vc.stop_record()
            await self.vc.disconnect()
            self.vc = None
            logger.info("🔇 Left voice channel and stopped recording.")

    async def save_recording(self):
        """Stops the current recording, saves it, and immediately starts a new one."""
        if not self.vc or not self.vc.is_recording():
            logger.warning("Not recording, cannot save.")
            return

        wav_bytes = await self.vc.stop_record()

        # Immediately start the next recording to minimize downtime
        self.vc.record(lambda e: logger.error(f"Error in recording callback: {e}"))
        logger.info("Started next recording...")

        timestamp = int(time.time())
        filename_wav = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.wav")
        filename_mp3 = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.mp3")

        with open(filename_wav, 'wb') as f:
            f.write(wav_bytes)

        cmd = [
            "ffmpeg", "-y", "-i", filename_wav,
            "-c:a", "libmp3lame", "-b:a", "192k", filename_mp3
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"Recording saved: {filename_mp3}")
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg failed: {e.stderr.decode()}")
        finally:
            if os.path.exists(filename_wav):
                os.remove(filename_wav)

    def play_sound(self, filepath: str):
        if self.vc and os.path.isfile(filepath):
            # The audiorec client might not have a `play` method directly
            # This might need to be adapted depending on the library's capabilities
            # For now, we assume it has a similar interface to the default client
            try:
                self.vc.play(discord.FFmpegPCMAudio(filepath))
            except AttributeError:
                logger.error("The voice client does not support the 'play' method.")
        else:
            logger.warning(f"Sound file '{filepath}' not found or VC is None")
