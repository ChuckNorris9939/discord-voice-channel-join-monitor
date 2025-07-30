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
    """Manages voice channel activity using a custom buffer and audiorec."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.vc: NativeVoiceClient | None = None
        self.audio_buffer = bytearray()
        self.buffer_lock = threading.Lock()
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.last_trigger_time = {}
        self._last_ok_time = 0.0
        self._last_stt_text: str = ""
        self.stt_task = None
        self.trim_task = None

        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _audio_callback(self, user, data):
        """This is called by the audiorec library for each audio packet."""
        with self.buffer_lock:
            self.audio_buffer.extend(data)

    async def _trim_buffer_loop(self):
        """Periodically trims the audio buffer to the max size."""
        while True:
            try:
                await asyncio.sleep(60) # Trim every minute
                with self.buffer_lock:
                    if len(self.audio_buffer) > MAX_BUFFER_SIZE:
                        amount_to_delete = len(self.audio_buffer) - MAX_BUFFER_SIZE
                        del self.audio_buffer[:amount_to_delete]
                        logger.debug(f"Trimmed audio buffer, removed {amount_to_delete} bytes.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in trim buffer loop: {e}", exc_info=True)

    async def stt_loop(self):
        """Periodically processes the audio buffer for trigger phrases."""
        while True:
            try:
                await asyncio.sleep(PROCESS_INTERVAL_S)
                with self.buffer_lock:
                    if len(self.audio_buffer) < WINDOW_BYTES_MIN:
                        continue
                    window = self.audio_buffer[-min(len(self.audio_buffer), WINDOW_BYTES_MAX):]
                    buf_copy = bytes(window)

                text = await self.bot.loop.run_in_executor(None, self._recognize_audio, buf_copy)
                if text:
                    self._handle_triggers(text)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in STT loop: {e}", exc_info=True)

    def _recognize_audio(self, pcm_data: bytes) -> str:
        """Perform STT on the given PCM data."""
        try:
            # audiorec provides raw PCM data, so we can use AudioData directly
            audio_data = sr.AudioData(pcm_data, SAMPLERATE, BYTES_PER_SAMPLE)
            return self.recognizer.recognize_google(audio_data, language="de-DE")
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            logger.error(f"Google STT request error: {e}")
            return ""

    def _handle_triggers(self, text: str):
        text = text.lower().strip()
        if text == self._last_stt_text or not text:
            return

        self._last_stt_text = text
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

    def save_recording(self):
        """Saves the entire buffer to an MP3 file."""
        with self.buffer_lock:
            pcm_data = bytes(self.audio_buffer)
            # We don't clear the buffer here, the trimmer will handle it

        timestamp = int(time.time())
        filename_wav = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.wav")
        filename_mp3 = os.path.join(OUTPUT_DIR, f"garmin_recording_{timestamp}.mp3")

        with open(filename_wav, 'wb') as f:
            f.write(pcm_data)

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
            try:
                self.vc.play(discord.FFmpegPCMAudio(filepath))
            except AttributeError:
                logger.error("The voice client does not support the 'play' method.")
        else:
            logger.warning(f"Sound file '{filepath}' not found or VC is None")

    async def join_channel(self, ctx):
        channel = ctx.author.voice.channel
        if ctx.voice_client is not None:
            self.vc = ctx.voice_client
            return await self.vc.move_to(channel)

        self.vc = await channel.connect(cls=NativeVoiceClient)
        self.vc.record(self._audio_callback)
        self.stt_task = self.bot.loop.create_task(self.stt_loop())
        self.trim_task = self.bot.loop.create_task(self._trim_buffer_loop())
        logger.info(f"🔊 Joined and started recording in '{channel.name}'")

    async def leave_channel(self, ctx):
        if self.stt_task:
            self.stt_task.cancel()
            self.stt_task = None
        if self.trim_task:
            self.trim_task.cancel()
            self.trim_task = None

        if self.vc:
            if self.vc.is_recording():
                await self.vc.stop_record()
            await self.vc.disconnect()
            self.vc = None
            logger.info("🔇 Left voice channel and stopped recording.")
