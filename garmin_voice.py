import discord
import speech_recognition as sr
from discord.ext import voice_recv
import wave
import os
from collections import deque
import threading
import time

# --- Constants ---
RECORD_SECONDS = 10 * 60  # 10 minutes
SAMPLERATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2
BUFFER_SIZE = int(RECORD_SECONDS * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
SOUND_FILE = "sounds/garmin_dingding.wav"
OUTPUT_DIR = "garmin-output"

class GarminVoiceManager:
    def __init__(self, bot):
        self.bot = bot
        self.audio_buffer = deque(maxlen=BUFFER_SIZE)
        self.recognizer = sr.Recognizer()
        self.is_processing = False
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.vc = None

    def callback(self, user, data: voice_recv.VoiceData):
        self.audio_buffer.extend(data.pcm)

        if not self.is_processing:
            threading.Thread(target=self._process_audio_data).start()

    def _process_audio_data(self):
        self.is_processing = True
        try:
            # Create a temporary WAV file from the buffer for speech recognition
            temp_wav_path = os.path.join(OUTPUT_DIR, f"temp_audio_{int(time.time())}.wav")
            with wave.open(temp_wav_path, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(BYTES_PER_SAMPLE)
                wf.setframerate(SAMPLERATE)
                wf.writeframes(bytes(self.audio_buffer))

            with sr.AudioFile(temp_wav_path) as source:
                audio = self.recognizer.record(source)

            try:
                text = self.recognizer.recognize_google(audio, language='de-DE')
                if "okay garmin video speichern" in text.lower():
                    self.save_recording()
                    self.play_sound()
            except sr.UnknownValueError:
                pass  # No speech detected
            except sr.RequestError as e:
                print(f"Could not request results from Google Speech Recognition service; {e}")

            os.remove(temp_wav_path)

        finally:
            self.is_processing = False

    def save_recording(self):
        filename = f"garmin_recording_{int(time.time())}.wav"
        filepath = os.path.join(OUTPUT_DIR, filename)

        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(BYTES_PER_SAMPLE)
            wf.setframerate(SAMPLERATE)
            wf.writeframes(bytes(self.audio_buffer))
        print(f"Recording saved to {filepath}")

    def play_sound(self):
        if os.path.exists(SOUND_FILE):
            source = discord.FFmpegPCMAudio(SOUND_FILE)
            if self.vc:
                self.vc.play(source)
        else:
            print(f"Sound file not found: {SOUND_FILE}")

    async def join_channel(self, channel):
        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        self.vc.listen(voice_recv.BasicSink(self.callback))

    async def leave_channel(self):
        if self.vc:
            await self.vc.disconnect()
            self.vc = None
