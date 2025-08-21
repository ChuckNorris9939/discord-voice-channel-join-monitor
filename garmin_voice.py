import os
import sys
import time
import wave
import threading
import difflib
import logging
import asyncio
import queue
import io
from typing import Final, Dict, Optional, List, Tuple
from pathlib import Path
from collections import defaultdict, OrderedDict

import discord
import speech_recognition as sr
from discord.ext import voice_recv
from pydub import AudioSegment
from pydub.utils import make_chunks

try:
    import vosk  # optional, only needed for offline STT
except ImportError:
    vosk = None

# --------------------------------------------------
# Logging setup - now handled by config_loader.py
# --------------------------------------------------
logger = logging.getLogger(__name__)

# Ensure logs directory exists
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, "data", "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

import config_loader as cfg

# ==================================================
# Audio / Recording constants
# ==================================================
SAMPLERATE: Final[int] = 48000  # Discord standard
CHANNELS: Final[int] = 2  # stereo
BYTES_PER_SAMPLE: Final[int] = 2  # 16-bit
SAMPLE_WIDTH: Final[int] = 2  # 16-bit PCM

# Frame size for Discord (20ms @ 48kHz stereo)
FRAME_DURATION_MS: Final[float] = 20.0
FRAME_SIZE_SAMPLES: Final[int] = int(FRAME_DURATION_MS / 1000 * SAMPLERATE)
FRAME_SIZE_BYTES: Final[int] = FRAME_SIZE_SAMPLES * CHANNELS * BYTES_PER_SAMPLE

# Buffer management
MAX_BUFFER_DURATION_S: Final[int] = int(os.getenv("GARMIN_MAX_BUFFER_DURATION", "600"))  # 10 minutes max per user
MAX_BUFFER_SIZE: Final[int] = cfg.GARMIN_RECORD_SECONDS * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE

# STT processing
PROCESS_INTERVAL_S: Final[float] = float(os.getenv("GARMIN_PROCESS_INTERVAL", "3.0"))
RECOGNITION_WINDOW_S: Final[float] = 3.0
MIN_WINDOW_S: Final[float] = 0.7
WINDOW_BYTES_MAX: Final[int] = int(RECOGNITION_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
WINDOW_BYTES_MIN: Final[int] = int(MIN_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)

# Path configuration
SOUNDS_DIR = os.path.join(SCRIPT_DIR, "assets", "sounds")
TEMP_DIR = os.path.join(SCRIPT_DIR, "data", "temp")
OUTPUT_DIR: Final[str] = os.path.join(SCRIPT_DIR, "data", "garmin-output")

# Trigger phrase detection
SOUND_DING = os.path.join(SOUNDS_DIR, "garmin_ding.wav")
SOUND_DINGDING = os.path.join(SOUNDS_DIR, "garmin_dingding.wav")

TRIGGERS = [
    {
        "name": "ding",
        "phrase": "okay garmin",
        "threshold": 0.7,
        "sound": SOUND_DING,
        "save": False,
    },
    {
        "name": "ding2",
        "phrase": "okay garmin video speichern",
        "threshold": 0.7,
        "sound": SOUND_DINGDING,
        "save": True,
    },
    {
        "name": "save",
        "phrase": "video speichern",
        "threshold": 0.7,
        "sound": SOUND_DINGDING,
        "save": True,
    },
]

TRIGGER_COOLDOWN_S: Final[int] = 5


class TimestampedAudioBuffer:
    """Manages audio buffer with timestamp tracking for synchronized playback."""
    
    def __init__(self, user_id: int, username: str, start_time: float):
        self.user_id = user_id
        self.username = username
        self.start_time = start_time
        
        # Store audio data with timestamps
        self.audio_frames: OrderedDict[float, bytes] = OrderedDict()
        self.lock = threading.Lock()
        self.last_activity = time.time()
        self.first_audio_time: Optional[float] = None
        self.last_audio_time: Optional[float] = None
        
    def add_audio(self, pcm_data: bytes, timestamp: float):
        """Add PCM audio data with timestamp."""
        with self.lock:
            relative_time = timestamp - self.start_time
            self.audio_frames[relative_time] = pcm_data
            
            if self.first_audio_time is None:
                self.first_audio_time = relative_time
            self.last_audio_time = relative_time
            self.last_activity = timestamp
            
            # Limit buffer size by removing old frames
            max_frames = (MAX_BUFFER_DURATION_S * 1000) // int(FRAME_DURATION_MS)
            if len(self.audio_frames) > max_frames:
                # Remove oldest frames
                for _ in range(len(self.audio_frames) - max_frames):
                    self.audio_frames.popitem(last=False)
    
    def create_continuous_segment(self, total_duration_s: float) -> Optional[AudioSegment]:
        """
        Create a continuous audio segment with proper timing.
        Fills gaps with silence to maintain synchronization.
        """
        with self.lock:
            if not self.audio_frames:
                # Return silence for the entire duration if no audio
                duration_ms = int(total_duration_s * 1000)
                return AudioSegment.silent(duration=duration_ms, frame_rate=SAMPLERATE)
            
            # Build continuous audio by iterating through expected time slots
            continuous_audio = bytearray()
            expected_frame_duration = FRAME_DURATION_MS / 1000.0  # in seconds
            num_frames = int(total_duration_s / expected_frame_duration)
            
            # Sort frames by timestamp
            sorted_frames = sorted(self.audio_frames.items())
            frame_index = 0
            
            for i in range(num_frames):
                current_time = i * expected_frame_duration
                
                # Check if we have audio for this time slot
                audio_found = False
                
                # Look for a frame within tolerance of current time
                while frame_index < len(sorted_frames):
                    frame_time, frame_data = sorted_frames[frame_index]
                    
                    # Check if this frame belongs to current time slot
                    if abs(frame_time - current_time) < expected_frame_duration / 2:
                        continuous_audio.extend(frame_data)
                        audio_found = True
                        frame_index += 1
                        break
                    elif frame_time > current_time + expected_frame_duration / 2:
                        # Frame is for a future time slot
                        break
                    else:
                        # Frame is from past, skip it
                        frame_index += 1
                
                if not audio_found:
                    # Add silence for this time slot
                    silence_frame = b'\x00' * FRAME_SIZE_BYTES
                    continuous_audio.extend(silence_frame)
            
            # Create AudioSegment from continuous audio
            try:
                segment = AudioSegment(
                    bytes(continuous_audio),
                    sample_width=SAMPLE_WIDTH,
                    frame_rate=SAMPLERATE,
                    channels=CHANNELS
                )
                
                # Ensure exact duration
                target_duration_ms = int(total_duration_s * 1000)
                if len(segment) > target_duration_ms:
                    segment = segment[:target_duration_ms]
                elif len(segment) < target_duration_ms:
                    silence_padding = AudioSegment.silent(
                        duration=target_duration_ms - len(segment),
                        frame_rate=SAMPLERATE
                    )
                    segment = segment + silence_padding
                
                return segment
                
            except Exception as e:
                logger.error(f"Error creating continuous segment for user {self.username}: {e}")
                return None
    
    def clear(self):
        """Clear the audio buffer."""
        with self.lock:
            self.audio_frames.clear()
            self.first_audio_time = None
            self.last_audio_time = None


class GarminVoiceManager:
    """Voice listener that reacts on trigger phrases and plays sounds."""
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.user_buffers: Dict[int, TimestampedAudioBuffer] = {}
        self._buffers_lock = threading.Lock()
        
        # Recording state
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        
        # STT combined buffer for trigger detection
        self.stt_buffer = bytearray()
        self._stt_buffer_lock = threading.Lock()
        
        # STT processing
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
        self._last_ok_time: float = 0.0
        self._last_stt_text: str = ""
        
        # Voice connection
        self.vc: Optional[voice_recv.VoiceRecvClient] = None
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        logger.info("GarminVoiceManager initialized with timestamped continuous recording")
    
    # ------------------------- Discord voice callbacks -------------------------
    def callback(self, user: Optional[discord.User], data: voice_recv.VoiceData):
        """Callback for incoming audio data with timestamps."""
        try:
            if not self.is_recording:
                return
            
            current_time = time.time()
            
            # Handle user audio buffering
            if user is not None:
                user_id = user.id
                username = user.name
            else:
                # Skip unknown users
                return
            
            # Add to timestamped user buffer
            with self._buffers_lock:
                if user_id not in self.user_buffers:
                    self.user_buffers[user_id] = TimestampedAudioBuffer(
                        user_id, username, self.recording_start_time
                    )
                    logger.info(f"Created timestamped buffer for user {username} (ID: {user_id})")
                
                self.user_buffers[user_id].add_audio(data.pcm, current_time)
            
            # Add to STT buffer for trigger detection
            if cfg.STT_ENABLED:
                with self._stt_buffer_lock:
                    self.stt_buffer.extend(data.pcm)
                    
                    # Limit STT buffer size
                    max_stt_buffer = WINDOW_BYTES_MAX * 2
                    if len(self.stt_buffer) > max_stt_buffer:
                        self.stt_buffer = self.stt_buffer[-max_stt_buffer:]
                
                # Process STT if enough time has passed
                if (len(self.stt_buffer) >= WINDOW_BYTES_MIN and 
                    current_time - self.last_process_time >= PROCESS_INTERVAL_S):
                    
                    try:
                        if not self.stt_queue.full():
                            self.stt_queue.put_nowait(current_time)
                            self.last_process_time = current_time
                    except queue.Full:
                        pass
                        
        except Exception as e:
            logger.error(f"Error in audio callback: {e}")
    
    # ------------------------------ Speech-rec thread ---------------------------
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
                request_time = self.stt_queue.get(timeout=1.0)
                self._process_stt_buffer()
                self.stt_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in STT worker: {e}")
    
    def _process_stt_buffer(self):
        """Process STT buffer for trigger phrases."""
        self.is_processing = True
        tmp_path = None
        try:
            # Get a copy of the STT buffer
            with self._stt_buffer_lock:
                if len(self.stt_buffer) < WINDOW_BYTES_MIN:
                    return
                
                window_size = min(len(self.stt_buffer), WINDOW_BYTES_MAX)
                window = bytes(self.stt_buffer[-window_size:])
            
            # Speech-to-Text
            if cfg.STT_ENGINE == "vosk":
                import audioop, json
                mono = audioop.tomono(window, BYTES_PER_SAMPLE, 0.5, 0.5)
                pcm16k, _ = audioop.ratecv(mono, BYTES_PER_SAMPLE, 1, SAMPLERATE, 16_000, None)
                rec = vosk.KaldiRecognizer(self.vosk_model, 16_000)
                rec.AcceptWaveform(pcm16k)
                text = json.loads(rec.Result()).get("text", "")
            else:  # Google
                import socket
                socket.setdefaulttimeout(8)
                tmp_path = os.path.join(TEMP_DIR, f"temp_{int(time.time()*1000)}.wav")
                
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(BYTES_PER_SAMPLE)
                    wf.setframerate(SAMPLERATE)
                    wf.writeframes(window)
                
                with sr.AudioFile(tmp_path) as source:
                    audio = self.recognizer.record(source)
                
                try:
                    text = self.recognizer.recognize_google(audio, language="de-DE")
                except sr.UnknownValueError:
                    text = ""
                except sr.RequestError as e:
                    logger.error(f"Google STT request error: {e}")
                    text = ""
            
            # Process recognized text
            text = text.lower().strip()
            if text and text != self._last_stt_text:
                self._last_stt_text = text
                
                if cfg.GARMIN_STT_OUTPUT_ENABLED:
                    logger.info(f"STT[{cfg.STT_ENGINE}]: '{text}'")
                
                # Check for trigger phrases
                now = time.time()
                for trig in TRIGGERS:
                    phrase = trig["phrase"]
                    matched = phrase in text or difflib.SequenceMatcher(None, phrase, text).ratio() >= trig["threshold"]
                    
                    if matched:
                        # Check cooldown
                        if now - self.last_trigger_time[trig["name"]] < TRIGGER_COOLDOWN_S:
                            continue
                        
                        # Special logic for "save" trigger
                        if trig["name"] == "save" and now - self._last_ok_time > 5:
                            continue
                        
                        # Fire trigger
                        self.last_trigger_time[trig["name"]] = now
                        if trig["name"] == "ding":
                            self._last_ok_time = now
                        
                        if trig["save"]:
                            self.save_recording()
                        
                        self.play_sound(trig["sound"])
                        break
                        
        except Exception as e:
            logger.error(f"Error during STT processing: {e}")
        finally:
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            self.is_processing = False
    
    # ------------------------------ Recording management ---------------------------
    def save_recording(self):
        """Save current recording with synchronized timestamps."""
        try:
            if not self.is_recording:
                logger.warning("Not recording, nothing to save")
                return
            
            current_time = time.time()
            total_duration_s = current_time - self.recording_start_time
            
            logger.info(f"Saving recording with total duration: {total_duration_s:.1f}s")
            
            timestamp = time.strftime('%d.%m.%Y_%H-%M', time.localtime())
            base_filename = f"recording_{timestamp}"
            
            with self._buffers_lock:
                user_segments = {}
                active_users = []
                
                # Create continuous segments for all users
                for user_id, buffer in self.user_buffers.items():
                    segment = buffer.create_continuous_segment(total_duration_s)
                    
                    if segment:
                        user_segments[user_id] = segment
                        active_users.append(buffer.username)
                        
                        # Save individual user recording
                        user_filename = f"{base_filename}_user_{self._sanitize_filename(buffer.username)}.wav"
                        user_path = os.path.join(OUTPUT_DIR, user_filename)
                        
                        try:
                            segment.export(user_path, format="wav")
                            duration_s = len(segment) / 1000.0
                            logger.info(f"Saved individual recording for {buffer.username}: {user_path} ({duration_s:.1f}s)")
                        except Exception as e:
                            logger.error(f"Error saving individual recording for {buffer.username}: {e}")
                
                # Create mixed recording if we have audio
                if user_segments:
                    mixed_filename = f"{base_filename}.wav"
                    mixed_path = os.path.join(OUTPUT_DIR, mixed_filename)
                    
                    try:
                        mixed_segment = self._mix_equal_length_segments(user_segments)
                        
                        if mixed_segment:
                            mixed_segment.export(mixed_path, format="wav")
                            duration_s = len(mixed_segment) / 1000.0
                            logger.info(f"Saved mixed recording: {mixed_path} ({duration_s:.1f}s, {len(active_users)} users)")
                        else:
                            logger.warning("No mixed audio generated")
                            
                    except Exception as e:
                        logger.error(f"Error saving mixed recording: {e}")
                else:
                    logger.warning("No user audio to save")
                
                # Clear buffers after saving
                for buffer in self.user_buffers.values():
                    buffer.clear()
                
                # Clear STT buffer
                with self._stt_buffer_lock:
                    self.stt_buffer.clear()
                
                logger.info(f"Recording saved: {len(active_users)} users, all synchronized to {total_duration_s:.1f}s")
                
        except Exception as e:
            logger.error(f"Error during save_recording: {e}")
    
    def _mix_equal_length_segments(self, user_segments: Dict[int, AudioSegment]) -> Optional[AudioSegment]:
        """
        Mix equal-length audio segments with proper gain staging.
        """
        try:
            if not user_segments:
                return None
            
            segments = list(user_segments.values())
            
            if len(segments) == 1:
                return segments[0]
            
            # All segments should have the same length already
            target_length = len(segments[0])
            
            # Start with silence as base
            mixed = AudioSegment.silent(duration=target_length, frame_rate=SAMPLERATE)
            
            # Calculate appropriate gain reduction
            # Use logarithmic scaling for better results with multiple users
            num_users = len(segments)
            if num_users <= 2:
                gain_reduction = 0  # No reduction for 1-2 users
            elif num_users <= 4:
                gain_reduction = -3  # -3dB for 3-4 users
            elif num_users <= 8:
                gain_reduction = -6  # -6dB for 5-8 users
            else:
                gain_reduction = -9  # -9dB for 9+ users
            
            # Mix all segments
            for segment in segments:
                # Apply gain reduction before mixing
                if gain_reduction != 0:
                    adjusted = segment + gain_reduction
                else:
                    adjusted = segment
                
                # Overlay onto mixed track
                mixed = mixed.overlay(adjusted, position=0)
            
            # Apply compression/normalization to prevent clipping
            mixed = self._normalize_audio(mixed)
            
            return mixed
            
        except Exception as e:
            logger.error(f"Error mixing equal-length segments: {e}")
            return None
    
    def _normalize_audio(self, segment: AudioSegment) -> AudioSegment:
        """
        Normalize audio to prevent clipping while maintaining dynamics.
        """
        try:
            # Get the peak amplitude
            peak = segment.max
            
            if peak <= 0:
                return segment
            
            # Calculate headroom (use 95% of maximum to avoid hard clipping)
            target_peak = int(32767 * 0.95)
            
            # Only reduce if we're over the target
            if peak > target_peak:
                # Calculate reduction in dB
                reduction_ratio = target_peak / peak
                reduction_db = 20 * math.log10(reduction_ratio) if reduction_ratio > 0 else -20
                
                # Apply gain reduction
                normalized = segment + reduction_db
                logger.debug(f"Applied normalization: {reduction_db:.1f}dB reduction")
                return normalized
            
            return segment
            
        except Exception as e:
            logger.error(f"Error normalizing audio: {e}")
            return segment
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a username for use in filenames."""
        forbidden = '<>:"/\\|?*'
        sanitized = ''.join('_' if c in forbidden or ord(c) < 32 else c for c in name)
        sanitized = sanitized.strip().rstrip('. ')
        return sanitized[:64] if sanitized else 'unknown'
    
    def play_sound(self, filepath: str):
        """Play a sound file in the voice channel."""
        if self.vc and os.path.isfile(filepath):
            try:
                self.vc.play(discord.FFmpegPCMAudio(filepath))
            except Exception as e:
                logger.error(f"Error playing sound {filepath}: {e}")
        else:
            logger.warning(f"Cannot play sound: VC={self.vc is not None}, File exists={os.path.isfile(filepath)}")
    
    # ---------------------- Voice connection helpers ---------------------------
    async def join_channel(self, channel: discord.VoiceChannel):
        """Connect to a voice channel and start listening."""
        try:
            # Disconnect first if already connected
            if self.is_connected():
                await self.leave_channel()
                await asyncio.sleep(0.5)
            
            # Connect to the voice channel
            self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
            self.vc.listen(voice_recv.BasicSink(self.callback))
            
            # Start recording
            self.recording_start_time = time.time()
            self.is_recording = True
            
            # Start STT worker if enabled
            if cfg.STT_ENABLED:
                self._start_stt_worker()
                logger.info(f"🔊 Joined voice channel '{channel.name}' (STT: {cfg.STT_ENGINE})")
            else:
                logger.info(f"🔊 Joined voice channel '{channel.name}' (STT disabled)")
            
            logger.info(f"Recording started at {self.recording_start_time}")
            
        except Exception as e:
            logger.error(f"Failed to join voice channel '{channel.name}': {e}")
            if self.vc:
                try:
                    await self.vc.disconnect()
                except:
                    pass
                self.vc = None
            self.is_recording = False
            raise
    
    def is_connected(self) -> bool:
        """Check if connected to a voice channel."""
        return self.vc is not None and self.vc.is_connected()
    
    async def leave_channel(self):
        """Disconnect from the current voice channel."""
        try:
            self._stop_stt_worker()
            
            # Stop recording
            self.is_recording = False
            
            # Clear all buffers
            with self._buffers_lock:
                self.user_buffers.clear()
            
            with self._stt_buffer_lock:
                self.stt_buffer.clear()
            
            if self.vc:
                if self.vc.is_connected():
                    await self.vc.disconnect()
                    logger.info("🔇 Left voice channel")
                self.vc = None
                
        except Exception as e:
            logger.error(f"Error in leave_channel: {e}")
            self.vc = None
    
    def get_recording_health(self) -> dict:
        """Get current recording system health status."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time if self.is_recording else 0
        
        with self._buffers_lock:
            active_users = len(self.user_buffers)
            total_frames = sum(len(buf.audio_frames) for buf in self.user_buffers.values())
            estimated_size = total_frames * FRAME_SIZE_BYTES
        
        return {
            "connected": self.is_connected(),
            "recording": self.is_recording,
            "recording_duration": recording_duration,
            "max_recording_duration": cfg.GARMIN_RECORD_SECONDS,
            "buffer_size": estimated_size,
            "recording_errors": 0,
            "max_errors": 5,
            "is_processing": self.is_processing,
            "stt_enabled": cfg.STT_ENABLED,
            "stt_engine": cfg.STT_ENGINE if cfg.STT_ENABLED else "disabled",
            "last_process_time": self.last_process_time,
            "audio_pipeline_healthy": True,
            "audio_callback_count": total_frames,
            "audio_callback_errors": 0,
            "time_since_last_audio": 0,
            "audio_callback_rate": 0,
            "last_chunk_size": 0,
            "active_users": active_users,
            "total_user_buffer_size": estimated_size,
            "user_buffers": {}
        }# Add math import for normalization
import math


