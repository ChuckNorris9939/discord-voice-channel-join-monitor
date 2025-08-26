import os
import time
import wave
import threading
import difflib
import logging
import asyncio
import queue
import json
import concurrent.futures
import signal
from typing import Final, Dict, Optional, List, Any
from pathlib import Path
from datetime import datetime

import discord
from discord.sinks import MP3Sink
import speech_recognition as sr
from pydub import AudioSegment

try:
    import vosk  # optional, only needed for offline STT
except ImportError:
    vosk = None

# --------------------------------------------------
# Logging setup - load config first to get proper log level
# --------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

import config_loader as cfg

# Load config first to get proper log level
cfg.load_all_settings()

# Set up logger with correct log level from config
logger = logging.getLogger(__name__)
# Apply log level from config if available
if hasattr(cfg, 'LOG_LEVEL'):
    log_level = getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(log_level)
    # Also ensure root logger allows this level
    root_logger = logging.getLogger()
    if root_logger.level > log_level:
        root_logger.setLevel(log_level)
    logger.info(f"🔧 garmin_voice.py logger set to {cfg.LOG_LEVEL} level (numeric: {log_level})")

# Import NumPy for advanced audio processing
import numpy as np

# Global thread pool for audio processing
_audio_thread_pool = None

# ==================================================
# LiveSTTMP3Sink - Native py-cord Sink with real-time STT + MP3 recording
# ==================================================

class LiveSTTMP3Sink(MP3Sink):
    """
    Advanced hybrid sink that extends MP3Sink with real-time STT using py-cord's native write() callback.
    Eliminates the need for auto-save timers and provides true real-time STT processing.
    """
    
    def __init__(self, garmin_manager=None, **kwargs):
        super().__init__(**kwargs)
        self.garmin_manager = garmin_manager
        logger.info("🎯 LiveSTTMP3Sink initialized - Real-time STT + MP3 recording")
    
    def write(self, data, user):
        """
        Override MP3Sink.write() to add live STT processing.
        Called for every audio packet (~20ms frames) - provides real-time STT without file saving.
        
        Args:
            data: Raw PCM audio data from Discord (20ms frames, 48kHz, stereo)
            user: User ID (integer) who spoke
        """
        try:
            # 1. PRESERVE: Call original MP3Sink functionality for recording
            super().write(data, user)
            
            # 2. ADD: Live STT processing with raw PCM data
            if self.garmin_manager and cfg.STT_ENABLED and data:
                # Get user object for better logging
                user_obj = self.garmin_manager.bot.get_user(user) if user else None
                username = user_obj.name if user_obj else f"user_{user}"
                
                # Debug: Log audio data characteristics
                logger.debug(f"🎤 LiveSTT received audio from {username}: {len(data)} bytes PCM data")
                
                # Process PCM data for STT (data is already in correct format!)
                self.garmin_manager._process_stt_audio(user_obj or user, data)
                
        except Exception as e:
            logger.error(f"Error in LiveSTTMP3Sink.write(): {e}")
            # Ensure MP3 recording continues even if STT fails
            try:
                super().write(data, user)
            except:
                pass

# ==================================================
# Utility Functions
# ==================================================

def _signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global _audio_thread_pool
    if _audio_thread_pool:
        logger.info("🛑 Shutdown signal received, shutting down thread pool...")
        _audio_thread_pool.shutdown(wait=False)
        _audio_thread_pool = None
    exit(0)

def cleanup_audio_thread_pool():
    """Cleanup function to be called when the main program exits."""
    global _audio_thread_pool
    if _audio_thread_pool:
        logger.info("🧹 Cleaning up audio thread pool...")
        _audio_thread_pool.shutdown(wait=False)
        _audio_thread_pool = None
        logger.info("✅ Audio thread pool cleanup complete")

# Signal handlers removed - handled by main.py to avoid conflicts

# ==================================================
# Helper Functions
# ==================================================

def sanitize_filename(name: str) -> str:
    """Sanitize username for filename use."""
    forbidden = '<>:"/\\|?*'
    sanitized = ''.join('_' if c in forbidden or ord(c) < 32 else c for c in name)
    sanitized = sanitized.strip().rstrip('. ')
    return sanitized[:32] if sanitized else 'unknown'


# ==================================================
# Audio / Recording constants
# ==================================================
SAMPLERATE: Final[int] = 48000  # Discord standard
CHANNELS: Final[int] = 2  # stereo
BYTES_PER_SAMPLE: Final[int] = 2  # 16-bit



# STT processing
PROCESS_INTERVAL_S: Final[float] = float(os.getenv("GARMIN_PROCESS_INTERVAL", "3.0"))
RECOGNITION_WINDOW_S: Final[float] = 3.0
MIN_WINDOW_S: Final[float] = 0.7
WINDOW_BYTES_MAX: Final[int] = int(RECOGNITION_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
WINDOW_BYTES_MIN: Final[int] = int(MIN_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)

# Trigger phrase detection
TRIGGER_COOLDOWN_S: Final[int] = 5

# Audio processing configuration - Always enabled for optimal performance

AUDIO_EXPORT_BITRATE: Final[str] = "128k"  # High quality MP3 export

# OPTIMIZATION #5: Advanced Silence Detection Settings (Default)
SILENCE_VAD_THRESHOLD: Final[float] = float(os.getenv("GARMIN_SILENCE_VAD_THRESHOLD", "0.3"))  # Voice Activity Detection threshold (0.1-0.9)
SILENCE_SPECTRAL_THRESHOLD: Final[float] = float(os.getenv("GARMIN_SILENCE_SPECTRAL_THRESHOLD", "0.15"))  # Spectral energy threshold
SILENCE_MIN_DURATION_MS: Final[int] = int(os.getenv("GARMIN_SILENCE_MIN_DURATION_MS", "500"))  # Minimum silence duration to consider

# Path configuration
SOUNDS_DIR = os.path.join(SCRIPT_DIR, "data", "assets", "sounds")
TEMP_DIR = os.path.join(SCRIPT_DIR, "data", "temp")
OUTPUT_DIR: Final[str] = os.path.join(SCRIPT_DIR, "data", "garmin-output")
ALIGNED_RECORDINGS_DIR: Final[str] = os.path.join(SCRIPT_DIR, "data", "aligned-recordings")

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


# ==================================================
# GarminVoiceManager - Py-cord implementation with WaveSink
# ==================================================

class GarminVoiceManager:
    """Py-cord compatible voice manager for Garmin functionality."""
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.vc: Optional[discord.VoiceClient] = None
        self.current_sink: Optional[MP3Sink] = None
        self.recording_start_time: float = time.time()
        self.last_saved_filename: Optional[str] = None
        self.last_saved_timestamp: Optional[float] = None
        
        # Legacy user buffers for compatibility
        self.user_buffers: Dict[int, Any] = {}
        self._buffers_lock = threading.Lock()
        
        # STT processing
        self.stt_buffer = bytearray()
        self._stt_buffer_lock = threading.Lock()
        self.stt_queue = queue.Queue(maxsize=2)
        self.stt_worker_thread = None
        self.stt_worker_running = False
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        
        # STT state
        self.is_processing = False
        self.last_process_time = 0.0
        self.last_trigger_time = {t["name"]: 0.0 for t in TRIGGERS}
        self._last_ok_time: float = 0.0
        self._last_stt_text: str = ""
        
        # Vosk model (load only if available, graceful fallback)
        self.vosk_model = None
        try:
            if cfg.STT_ENABLED and cfg.STT_ENGINE == "vosk":
                if vosk is None:
                    logger.warning("STT_ENGINE='vosk' but 'vosk' package missing - disabling STT")
                    cfg.STT_ENABLED = False
                else:
                    model_path = Path(cfg.VOSK_MODEL_PATH)
                    if not model_path.exists():
                        logger.warning(f"Vosk model not found at '{model_path}' - disabling STT")
                        cfg.STT_ENABLED = False
                    else:
                        logger.info("Loading Vosk model from %s …", model_path)
                        self.vosk_model = vosk.Model(str(model_path))
                        logger.info("Vosk model loaded.")
            
            if not cfg.STT_ENABLED:
                logger.info("🔇 STT disabled - LiveSTTMP3Sink will work as standard MP3Sink")
                
        except Exception as e:
            logger.warning(f"Failed to initialize Vosk model: {e} - disabling STT")
            cfg.STT_ENABLED = False
            self.vosk_model = None
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(ALIGNED_RECORDINGS_DIR, exist_ok=True)
        
        # Update logger level in case main.py has overridden it
        self._update_logger_level()
        
        logger.info("GarminVoiceManager initialized with py-cord compatibility")
    
    def _update_logger_level(self):
        """Update logger level from config, ensuring it works even after main.py setup."""
        try:
            # Reload settings to get latest LOG_LEVEL
            cfg.load_all_settings()
            
            # Use the new apply_log_level function for consistent behavior
            if hasattr(cfg, 'apply_log_level'):
                cfg.apply_log_level()
                logger.debug(f"🔧 Logger level updated to {cfg.LOG_LEVEL} using cfg.apply_log_level()")
            else:
                # Fallback to manual method
                if hasattr(cfg, 'LOG_LEVEL'):
                    log_level = getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO)
                    logger.setLevel(log_level)
                    root_logger = logging.getLogger()
                    if root_logger.level > log_level:
                        root_logger.setLevel(log_level)
                    logger.debug(f"🔧 Logger level updated to {cfg.LOG_LEVEL} (fallback method)")
                
        except Exception as e:
            logger.warning(f"Failed to update logger level: {e}")

    # ==================================================
    # STT Processing Methods (copied from garmin_voice_old.py)
    # ==================================================
    
    def _process_stt_audio(self, user, pcm_data):
        """Process PCM audio data for STT (called from HybridSTTMP3Sink)."""
        try:
            username = user.name if user else "unknown"
            logger.debug(f"🔊 _process_stt_audio called: STT_ENABLED={cfg.STT_ENABLED}, PCM data size={len(pcm_data) if pcm_data else 0}, user={username}")
            
            if cfg.STT_ENABLED and pcm_data:
                with self._stt_buffer_lock:
                    old_buffer_size = len(self.stt_buffer)
                    self.stt_buffer.extend(pcm_data)
                    
                    # Limit STT buffer size
                    max_stt_buffer = WINDOW_BYTES_MAX * 2
                    if len(self.stt_buffer) > max_stt_buffer:
                        self.stt_buffer = self.stt_buffer[-max_stt_buffer:]
                    
                    logger.debug(f"🎵 STT buffer: {old_buffer_size} → {len(self.stt_buffer)} bytes")
                
                # Process STT if enough time has passed
                now = time.time()
                time_since_last = now - self.last_process_time
                buffer_ready = len(self.stt_buffer) >= WINDOW_BYTES_MIN
                
                logger.debug(f"⏰ STT timing: buffer_size={len(self.stt_buffer)}, min_required={WINDOW_BYTES_MIN}, buffer_ready={buffer_ready}, time_since_last={time_since_last:.1f}s, interval={PROCESS_INTERVAL_S}s")
                
                if buffer_ready and time_since_last >= PROCESS_INTERVAL_S:
                    logger.debug(f"🚀 Triggering STT processing: buffer has {len(self.stt_buffer)} bytes")
                    try:
                        if not self.stt_queue.full():
                            self.stt_queue.put_nowait(now)
                            self.last_process_time = now
                            logger.debug(f"✅ STT processing queued successfully")
                        else:
                            logger.warning(f"⚠️ STT queue is full, skipping processing")
                    except queue.Full:
                        logger.warning(f"⚠️ STT queue full exception")
            else:
                if not cfg.STT_ENABLED:
                    logger.debug(f"🔇 STT disabled, skipping processing")
                if not pcm_data:
                    logger.debug(f"🔇 No PCM data provided, skipping processing")
                        
        except Exception as e:
            logger.error(f"Error in STT audio processing: {e}")
    
    def _start_stt_worker(self):
        """Start the STT worker thread."""
        if not self.stt_worker_running:
            self.stt_worker_running = True
            self.stt_worker_thread = threading.Thread(target=self._stt_worker, daemon=True)
            self.stt_worker_thread.start()
            logger.info("🔥 STT worker thread started successfully")
    
    def _stop_stt_worker(self):
        """Stop the STT worker thread."""
        self.stt_worker_running = False
        if self.stt_worker_thread and self.stt_worker_thread.is_alive():
            self.stt_worker_thread.join(timeout=5)
            logger.debug("STT worker thread stopped")
    
    def _stt_worker(self):
        """Worker thread for STT processing."""
        logger.info("🎯 STT worker thread started, waiting for audio data...")
        while self.stt_worker_running:
            try:
                request_time = self.stt_queue.get(timeout=1.0)
                logger.debug(f"📥 STT worker received processing request at {request_time}")
                self._process_stt_buffer()
                self.stt_queue.task_done()
                logger.debug(f"✅ STT processing completed")
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in STT worker: {e}")
        logger.info("🛑 STT worker thread stopped")
    
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
                import audioop
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
                        self.play_sound(trig["sound"])
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
    
    # ==================================================
    # Voice Channel Management
    # ==================================================
    
    async def join_channel(self, channel: discord.VoiceChannel):
        """Connect to a voice channel with HybridSTTMP3Sink for both recording and live STT."""
        try:
            # Disconnect first if already connected
            if self.is_connected():
                await self.leave_channel()
                await asyncio.sleep(0.5)
            
            # Connect to the voice channel
            self.vc = await channel.connect()
            
            # Use LiveSTTMP3Sink for both MP3 recording AND real-time STT
            self.current_sink = LiveSTTMP3Sink(garmin_manager=self)
            
            self.vc.start_recording(
                self.current_sink,
                self._finished_callback,  # Same callback as before
                sync_start=True  # For more stable recording per official example
            )
            
            # Start STT worker thread if STT is enabled
            if cfg.STT_ENABLED:
                self._start_stt_worker()
                logger.info(f"🔊 Joined voice channel '{channel.name}' (HybridSink: MP3 Recording + Live STT[{cfg.STT_ENGINE}])")
            else:
                logger.info(f"🔊 Joined voice channel '{channel.name}' (HybridSink: MP3 Recording only - STT disabled)")
            
            self.recording_start_time = time.time()
            
        except Exception as e:
            logger.error(f"Failed to join voice channel '{channel.name}': {e}")
            if self.vc:
                try:
                    await self.vc.disconnect()
                except:
                    pass
                self.vc = None
            raise
    
    async def _finished_callback(self, sink: MP3Sink, channel=None):
        """Callback when recording finishes (py-cord MP3Sink compatible)."""
        try:
            timestamp = datetime.now().strftime('%d.%m.%y_%H-%M-%S')
            logger.info(f"Recording finished callback called for session: {timestamp}")
            
            # Check if we have any audio data
            if not hasattr(sink, 'audio_data') or not sink.audio_data:
                logger.warning("No audio data found in sink")
                return
            
            logger.info(f"Found audio data for {len(sink.audio_data)} users")
            
            # Save individual user files
            user_files = []
            for user_id, audio_data in sink.audio_data.items():
                try:
                    user = self.bot.get_user(user_id)
                    username = sanitize_filename(user.name) if user else f"user_{user_id}"
                    
                    logger.info(f"Processing audio for user {username} (ID: {user_id})")
                    
                    # Save to aligned recordings directory
                    filename = f"{timestamp}_{username}.mp3"
                    file_path = os.path.join(ALIGNED_RECORDINGS_DIR, filename)
                    
                    # Direct MP3 processing (from py-cord MP3Sink)
                    try:
                        # Reset file pointer and read MP3 data directly
                        audio_data.file.seek(0)
                        mp3_content = audio_data.file.read()
                        
                        logger.info(f"📦 Raw MP3 data size: {len(mp3_content)} bytes")
                        
                        # Check if MP3 file has content
                        if len(mp3_content) > 128:  # MP3 header minimum
                            # Save MP3 file directly
                            with open(file_path, 'wb') as f:
                                f.write(mp3_content)
                            
                            # Verify the saved file with pydub
                            try:
                                audio_segment = AudioSegment.from_mp3(file_path)
                                user_files.append(file_path)
                                logger.info(f"✅ Saved user recording: {filename} ({len(audio_segment)}ms)")
                                
                            except Exception as e:
                                logger.error(f"MP3 validation failed for {username}: {e}")
                                # Remove corrupted file
                                if os.path.exists(file_path):
                                    os.remove(file_path)
                        else:
                            logger.warning(f"⚠️ MP3 file too small ({len(mp3_content)} bytes), likely empty recording for {username}")
                        
                    except Exception as e:
                        logger.error(f"Error saving recording for {username}: {e}", exc_info=True)
                
                except Exception as e:
                    logger.error(f"Error processing user {user_id}: {e}")
            
            # Create mixed file if multiple users
            if len(user_files) > 1:
                try:
                    mixed_audio = None
                    for file_path in user_files:
                        try:
                            if file_path.endswith('.mp3'):
                                audio = AudioSegment.from_mp3(file_path)
                            else:
                                audio = AudioSegment.from_wav(file_path)
                            
                            if mixed_audio is None:
                                mixed_audio = audio
                            else:
                                mixed_audio = mixed_audio.overlay(audio)
                        except Exception as e:
                            logger.error(f"Error loading {file_path} for mixing: {e}")
                    
                    if mixed_audio:
                        mixed_filename = f"{timestamp}_mixed.mp3"
                        mixed_path = os.path.join(ALIGNED_RECORDINGS_DIR, mixed_filename)
                        mixed_audio.export(mixed_path, format="mp3", bitrate=AUDIO_EXPORT_BITRATE)
                        logger.info(f"✅ Saved mixed recording: {mixed_filename} ({len(mixed_audio)}ms)")
                        
                        # Copy to garmin-output
                        self._copy_to_garmin_output([mixed_path] + user_files, timestamp)
                        
                        self.last_saved_filename = f"{timestamp}_mixed.mp3"
                        self.last_saved_timestamp = time.time()
                        
                except Exception as e:
                    logger.error(f"Error creating mixed recording: {e}")
            elif len(user_files) == 1:
                # Single user - copy to garmin-output
                self._copy_to_garmin_output(user_files, timestamp)
                self.last_saved_filename = os.path.basename(user_files[0])
                self.last_saved_timestamp = time.time()
            
            if user_files:
                logger.info(f"✅ Recording session completed: {len(user_files)} user files saved")
                
                logger.info("✅ Files saved - STT processing happens in real-time via LiveSTTMP3Sink")
            else:
                logger.warning("⚠️ Recording session completed but no files saved")
            
            # Restart recording to continue capturing audio
            if self.vc and self.vc.is_connected():
                try:
                    logger.info("🔄 Restarting recording for continuous operation...")
                    # Use LiveSTTMP3Sink to preserve STT functionality after save
                    self.current_sink = LiveSTTMP3Sink(garmin_manager=self)
                    self.vc.start_recording(
                        self.current_sink,
                        self._finished_callback,
                        sync_start=True  # For more stable recording per official example
                    )
                    logger.info("✅ Recording restarted with LiveSTTMP3Sink - STT continues working")
                except Exception as restart_error:
                    logger.error(f"❌ Failed to restart recording: {restart_error}", exc_info=True)
                
        except Exception as e:
            logger.error(f"Critical error in recording callback: {e}", exc_info=True)
    
    def _copy_to_garmin_output(self, file_paths: List[str], timestamp: str):
        """Copy files to garmin-output directory."""
        import shutil
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        for file_path in file_paths:
            if os.path.exists(file_path):
                try:
                    # Determine destination filename
                    basename = os.path.basename(file_path)
                    if "_mixed.mp3" in basename:
                        dest_filename = f"{timestamp}_mixed.mp3"
                    else:
                        # Extract username and create user file
                        parts = basename.split('_')
                        if len(parts) >= 3:
                            username = parts[-1].replace('.mp3', '')
                            dest_filename = f"{timestamp}_user_{username}.mp3"
                        else:
                            dest_filename = basename
                    
                    dest_path = os.path.join(OUTPUT_DIR, dest_filename)
                    shutil.copy2(file_path, dest_path)
                    logger.info(f"📄 Copied: {basename} → {dest_filename}")
                    
                except Exception as e:
                    logger.error(f"Error copying {file_path}: {e}")
    
    def save_recording(self):
        """Save current recording - stops and restarts recording."""
        try:
            logger.info("🎯 save_recording() called")
            
            if not self.vc or not self.vc.is_connected():
                logger.error("❌ No voice connection active for save_recording")
                return "❌ Not connected to any voice channel"
                
            if not self.current_sink:
                logger.error("❌ No active recording sink for save_recording")
                return "❌ No active recording found"
            
            logger.info("💾 Triggering recording save...")
            
            # Stop current recording (this will trigger the callback)
            self.vc.stop_recording()
            logger.info("🔄 Recording stopped, callback will handle restart automatically")
            
            return "✅ Recording save initiated"
                
        except Exception as e:
            logger.error(f"❌ Error saving recording: {e}", exc_info=True)
            return f"❌ Error: {str(e)}"
    
    async def _restart_recording(self):
        """Restart recording after save."""
        try:
            await asyncio.sleep(0.5)  # Small delay
            if self.vc and self.vc.is_connected():
                # Create new LiveSTTMP3Sink for continued recording (preserves STT functionality)
                self.current_sink = LiveSTTMP3Sink(garmin_manager=self)
                
                self.vc.start_recording(
                    self.current_sink,
                    self._finished_callback,
                )
                logger.info("🔄 Recording restarted with LiveSTTMP3Sink - STT continues working")
        except Exception as e:
            logger.error(f"Error restarting recording: {e}")
    
    def play_sound(self, filepath: str):
        """Play a sound file in the voice channel."""
        if self.vc and self.vc.is_connected() and os.path.isfile(filepath):
            try:
                self.vc.play(discord.FFmpegPCMAudio(filepath))
            except Exception as e:
                logger.error(f"Error playing sound {filepath}: {e}")
        else:
            logger.warning(f"Cannot play sound: VC connected={self.vc.is_connected() if self.vc else False}, File exists={os.path.isfile(filepath)}")
    
    def is_connected(self) -> bool:
        """Check if connected to a voice channel."""
        return self.vc is not None and self.vc.is_connected()
    
    async def leave_channel(self):
        """Disconnect from the current voice channel."""
        try:
            # Stop STT worker first
            self._stop_stt_worker()
            
            # Stop recording
            if self.vc and self.vc.is_connected():
                try:
                    self.vc.stop_recording()
                    logger.debug("Stopped MP3 recording")
                except Exception as e:
                    logger.debug(f"Error stopping recording: {e}")
            
            # Clear sinks
            self.current_sink = None
            
            # Clear buffers
            with self._buffers_lock:
                self.user_buffers.clear()
            
            # Clear STT buffer
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
            self.current_sink = None
    
    def get_recording_health(self) -> dict:
        """Get current recording system health status."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time if self.is_connected() else 0
        
        # Calculate estimated buffer size based on recording duration
        # Assume stereo 16-bit 48kHz audio (~384 KB/s) for estimation
        estimated_buffer_size = int(recording_duration * 384 * 1024) if self.is_connected() and recording_duration > 0 else 0
        
        return {
            'connected': self.is_connected(),
            'recording_duration': recording_duration,
            'buffer_size': estimated_buffer_size,  # Estimated based on duration
            'recording_errors': 0,
            'max_errors': 5,
            'is_processing': False,
            'max_recording_duration': 600,
            'audio_pipeline_healthy': True,
            'audio_callback_count': 0,
            'audio_callback_errors': 0,
            'time_since_last_audio': 0,
            'audio_callback_rate': 0,
            'last_chunk_size': 0,
            'active_users': 0,
            'total_user_buffer_size': 0,
            'aligned_recording_active': self.is_connected(),
            'aligned_users': 0,
            'aligned_session_samples': 0,
            'aligned_session_duration_ms': recording_duration * 1000,
            'py_cord_recording': {
                'engine': 'py-cord',
                'sink_type': 'WaveSink',
                'active': self.is_connected()
            }
        }
    
    def get_recording_info(self) -> dict:
        """Get detailed recording information."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time if self.is_connected() else 0
        
        # Get actual file size if available
        actual_file_size = 0
        if self.last_saved_filename:
            try:
                file_path = os.path.join(OUTPUT_DIR, self.last_saved_filename)
                if os.path.exists(file_path):
                    actual_file_size = os.path.getsize(file_path)
            except Exception as e:
                logger.debug(f"Could not get file size: {e}")
        
        return {
            "recording_duration": recording_duration,
            "buffer_size": 0,  # Not applicable for py-cord
            "actual_file_size": actual_file_size,
            "active_users": 0,  # Will be updated when recording finishes
            "last_saved_filename": self.last_saved_filename,
            "last_saved_timestamp": self.last_saved_timestamp,
            "compressed_duration": None,
        }
