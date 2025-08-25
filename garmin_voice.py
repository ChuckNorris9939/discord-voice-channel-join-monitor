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
# Logging setup - now handled by config_loader.py
# --------------------------------------------------
logger = logging.getLogger(__name__)

# Import NumPy for advanced audio processing
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

import config_loader as cfg

# Global thread pool for audio processing
_audio_thread_pool = None

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
SAMPLE_WIDTH: Final[int] = 2  # 16-bit PCM

# Buffer management
MAX_BUFFER_DURATION_S: Final[int] = int(os.getenv("GARMIN_MAX_BUFFER_DURATION", "600"))  # 10 minutes max per user

# STT processing
PROCESS_INTERVAL_S: Final[float] = float(os.getenv("GARMIN_PROCESS_INTERVAL", "3.0"))
RECOGNITION_WINDOW_S: Final[float] = 3.0
MIN_WINDOW_S: Final[float] = 0.7
WINDOW_BYTES_MAX: Final[int] = int(RECOGNITION_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
WINDOW_BYTES_MIN: Final[int] = int(MIN_WINDOW_S * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)

# Audio processing configuration - Always enabled for optimal performance

# OPTIMIZATION #3: Audio Format Optimization Settings (MP3-only)
AUDIO_EXPORT_BITRATE: Final[str] = "192k"  # High quality MP3 export
AUDIO_EXPORT_QUALITY: Final[List[str]] = ["-q:a", "2"]  # High quality MP3 encoding
AUDIO_CHUNK_SIZE_MS: Final[int] = 30000  # Process audio in 30-second chunks for memory efficiency

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
        
        # Vosk model
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
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(ALIGNED_RECORDINGS_DIR, exist_ok=True)
        
        logger.info("GarminVoiceManager initialized with py-cord compatibility")
    
    async def join_channel(self, channel: discord.VoiceChannel):
        """Connect to a voice channel and start recording using py-cord."""
        try:
            # Disconnect first if already connected
            if self.is_connected():
                await self.leave_channel()
                await asyncio.sleep(0.5)
            
            # Connect to the voice channel
            self.vc = await channel.connect()
            
            # Use standard py-cord WaveSink for recording
            self.current_sink = MP3Sink()
            
            # Start recording with py-cord MP3Sink (official example)
            self.vc.start_recording(
                self.current_sink,
                self._finished_callback,
                sync_start=True  # For more stable recording per official example
            )
            
            logger.info(f"🔊 Joined voice channel '{channel.name}' (py-cord Recording: ON)")
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
            else:
                logger.warning("⚠️ Recording session completed but no files saved")
            
            # Restart recording to continue capturing audio
            if self.vc and self.vc.is_connected():
                try:
                    logger.info("🔄 Restarting recording for continuous operation...")
                    self.current_sink = MP3Sink()
                    self.vc.start_recording(
                        self.current_sink,
                        self._finished_callback,
                        sync_start=True  # For more stable recording per official example
                    )
                    logger.info("✅ Recording restarted successfully")
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
                # Create new sink for continued recording
                self.current_sink = MP3Sink()
                
                self.vc.start_recording(
                    self.current_sink,
                    self._finished_callback,
                )
                logger.info("🔄 Recording restarted with new sink")
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
            # Stop recording first
            if self.vc and self.vc.is_connected():
                try:
                    self.vc.stop_recording()
                    logger.debug("Stopped recording")
                except Exception as e:
                    logger.debug(f"Error stopping recording: {e}")
            
            # Clear current sink
            self.current_sink = None
            
            # Clear buffers
            with self._buffers_lock:
                self.user_buffers.clear()
            
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
