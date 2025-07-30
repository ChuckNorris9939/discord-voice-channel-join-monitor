import os
import time
import wave
import threading
import difflib
from typing import Final

import discord
import speech_recognition as sr
from discord.ext import voice_recv

# --------------------------------------------------
# Audio / Recording constants
# --------------------------------------------------
RECORD_SECONDS: Final[int] = 10 * 60     # keep the last 10 min in RAM
SAMPLERATE: Final[int] = 48_000          # Discord always uses 48 kHz
CHANNELS: Final[int] = 2                 # stereo PCM
BYTES_PER_SAMPLE: Final[int] = 2         # 16‑bit = 2 bytes

MAX_BUFFER_SIZE: Final[int] = (
    RECORD_SECONDS * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE
)

# One Opus frame = 20 ms → 960 samples @48 kHz
FRAMES_PER_BUFFER: Final[int] = 960
CHUNK_SIZE: Final[int] = FRAMES_PER_BUFFER * CHANNELS * BYTES_PER_SAMPLE  # 3 840 bytes

# --------------------------------------------------
# Trigger phrase detection
# --------------------------------------------------
SOUND_DING = "sounds/garmin_ding.wav"
SOUND_DINGDING = "sounds/garmin_dingding.wav"

TRIGGERS = [
    {
        "name": "save",
        "phrase": "okay garmin video speichern",
        "threshold": 0.90,
        "sound": SOUND_DINGDING,
        "save": True,
    },
    {
        "name": "ding",
        "phrase": "okay garmin",
        "threshold": 0.85,
        "sound": SOUND_DING,
        "save": False,
    },
]

TRIGGER_COOLDOWN_S: Final[int] = 5       # per‑trigger cooldown
RECOGNITION_WINDOW_S: Final[float] = 3    # analyse up to 3 s
MIN_WINDOW_S: Final[float] = 0.7          # at least 0.7 s of audio

WINDOW_BYTES_MAX: Final[int] = int(RECOGNITION_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
WINDOW_BYTES_MIN: Final[int] = int(MIN_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)

PROCESS_INTERVAL_S: Final[float] = 1.0    # seconds between STT attempts
OUTPUT_DIR: Final[str] = "garmin-output"


class GarminVoiceManager:
    """Voice listener that reacts on trigger phrases and plays sounds.

    Thread‑safe: uses `self._buf_lock` to protect the mutable `bytearray` so we
    don't hit `BufferError: Existing exports of data: object cannot be re-sized`
    when one thread is reading while another extends the buffer.
    """

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.audio_buffer = bytearray()
        self._buf_lock = threading.Lock()

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True

        self.is_processing = False
        self.last_process_time = 0.0
        self.last_trigger_time = {t["name"]: 0.0 for t in TRIGGERS}

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.vc: voice_recv.VoiceRecvClient | None = None

    # ------------------------- Discord voice callbacks -------------------------
    def callback(self, user: discord.User | None, data: voice_recv.VoiceData):
        self._append_to_buffer(data.pcm)
        now = time.time()
        if not self.is_processing and (now - self.last_process_time >= PROCESS_INTERVAL_S):
            threading.Thread(target=self._process_audio_data, daemon=True).start()

    def _append_to_buffer(self, chunk: bytes) -> None:
        """Thread‑safe append & trim."""
        with self._buf_lock:
            self.audio_buffer.extend(chunk)
            if len(self.audio_buffer) > MAX_BUFFER_SIZE:
                del self.audio_buffer[: len(self.audio_buffer) - MAX_BUFFER_SIZE]

    # ------------------------------ Speech‑rec thread ---------------------------
    def _process_audio_data(self):
        self.is_processing = True
        self.last_process_time = time.time()
        tmp_path = None
        try:
            # Grab a *copy* of the buffer under lock to avoid concurrent resize
            with self._buf_lock:
                buf_copy = bytes(self.audio_buffer)  # immutable copy → no exports

            if len(buf_copy) < WINDOW_BYTES_MIN:
                return  # not enough speech yet

            window = buf_copy[-min(len(buf_copy), WINDOW_BYTES_MAX):]

            tmp_path = os.path.join(OUTPUT_DIR, f"temp_{int(time.time()*1000)}.wav")
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(BYTES_PER_SAMPLE)
                wf.setframerate(SAMPLERATE)
                wf.writeframes(window)

            with sr.AudioFile(tmp_path) as source:
                audio = self.recognizer.record(source)
            try:
                text = self.recognizer.recognize_google(audio, language="de-DE").lower()
                print(f"STT: '{text}'")
                now = time.time()

                for trig in sorted(TRIGGERS, key=lambda t: len(t["phrase"]), reverse=True):
                    similarity = difflib.SequenceMatcher(None, text, trig["phrase"]).ratio()
                    if similarity >= trig["threshold"] and (
                        now - self.last_trigger_time[trig["name"]] >= TRIGGER_COOLDOWN_S
                    ):
                        self.last_trigger_time[trig["name"]] = now
                        if trig["save"]:
                            self.save_recording()
                        self.play_sound(trig["sound"])
                        break
            except sr.UnknownValueError:
                pass
            except sr.RequestError as e:
                print(f"Google SR request error: {e}")
        finally:
            if tmp_path and os.path.isfile(tmp_path):
                os.remove(tmp_path)
            self.is_processing = False

    # ------------------------------ Helpers ------------------------------------
    def save_recording(self):
        filename = f"garmin_recording_{int(time.time())}.wav"
        path = os.path.join(OUTPUT_DIR, filename)
        with self._buf_lock:  # copy under lock to avoid resize during write
            data = bytes(self.audio_buffer)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(BYTES_PER_SAMPLE)
            wf.setframerate(SAMPLERATE)
            wf.writeframes(data)
        print(f"🔸 Recording saved: {path}")

    def play_sound(self, filepath: str):
        if self.vc and os.path.isfile(filepath):
            self.vc.play(discord.FFmpegPCMAudio(filepath))
        else:
            print(f"🔸 Sound file '{filepath}' not found or VC is None")

    # ---------------------- Public coroutine helpers ---------------------------
    async def join_channel(self, channel: discord.VoiceChannel):
        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        self.vc.listen(voice_recv.BasicSink(self.callback))
        print(f"🔊 Listening in {channel.name}")

    async def leave_channel(self):
        if self.vc:
            await self.vc.disconnect()
            self.vc = None
            print("🔇 Disconnected")