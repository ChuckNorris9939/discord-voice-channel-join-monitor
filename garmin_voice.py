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
import speech_recognition as sr
from discord.ext import voice_recv
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

# Register signal handlers for clean shutdown
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

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

# OPTIMIZATION #6: Batch Processing Optimization Settings
BATCH_PROCESSING_ENABLED: Final[bool] = os.getenv("GARMIN_BATCH_PROCESSING_ENABLED", "true").lower() == "true"
MAX_CONCURRENT_SESSIONS: Final[int] = int(os.getenv("GARMIN_MAX_CONCURRENT_SESSIONS", "3"))  # Maximum concurrent recording sessions
SESSION_QUEUE_SIZE: Final[int] = int(os.getenv("GARMIN_SESSION_QUEUE_SIZE", "10"))  # Maximum queued sessions
BATCH_CLEANUP_INTERVAL: Final[int] = int(os.getenv("GARMIN_BATCH_CLEANUP_INTERVAL", "300"))  # Cleanup interval in seconds

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
# Aligned Recording Classes
# ==================================================

class UserWavWriter:
    """Thread-safe MP3 writer for a single user with padding support."""
    
    def __init__(self, file_path: str, user_id: int, username: str):
        self.file_path = file_path
        self.user_id = user_id
        self.username = username
        self.lock = threading.Lock()
        self.sample_cursor = 0  # Current position in samples since recording start
        self.audio_buffer = bytearray()  # Buffer for PCM data
        self.is_closed = False
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        logger.debug(f"UserWavWriter initialized for {username} (ID: {user_id}): {file_path}")
    
    def write_silence(self, num_samples: int):
        """Write silence padding for the specified number of samples."""
        if self.is_closed:
            logger.warning(f"Cannot write silence for {self.username}: audio buffer is closed")
            return
            
        if num_samples <= 0:
            logger.warning(f"Invalid num_samples for {self.username}: {num_samples}")
            return
        
        try:
            # Write silence efficiently - mono 16-bit samples
            if num_samples <= 240000:  # Up to ~5 seconds in one go
                silence_bytes = b'\x00\x00' * num_samples
                self.audio_buffer.extend(silence_bytes)
                self.sample_cursor += num_samples
            else:
                # Write in chunks for very large silence blocks
                chunk_size = 240000  # 5 seconds
                samples_written = 0
                
                while samples_written < num_samples:
                    chunk_samples = min(chunk_size, num_samples - samples_written)
                    silence_chunk = b'\x00\x00' * chunk_samples
                    
                    self.audio_buffer.extend(silence_chunk)
                    samples_written += chunk_samples
                    self.sample_cursor += chunk_samples
                
        except Exception as e:
            logger.error(f"💥 Error writing silence for {self.username}: {e}", exc_info=True)
    
    def write_audio(self, pcm_data: bytes):
        """Write PCM audio data to the audio buffer."""
        
        if self.is_closed:
            logger.warning(f"Cannot write audio for {self.username}: audio buffer is closed")
            return
            
        if not pcm_data:
            logger.warning(f"Cannot write audio for {self.username}: No PCM data provided")
            return
            
        try:
            # Always convert stereo to mono for optimal performance
            if len(pcm_data) % 4 == 0:  # Stereo 16-bit
                mono_data = bytearray()
                for i in range(0, len(pcm_data), 4):
                    # Take left channel (first 2 bytes)
                    mono_data.extend(pcm_data[i:i+2])
                pcm_data = bytes(mono_data)
            
            if pcm_data:  # Only write if we have data
                self.audio_buffer.extend(pcm_data)
                samples_written = len(pcm_data) // 2  # 16-bit samples
                self.sample_cursor += samples_written
            else:
                logger.warning(f"No PCM data to write for user {self.username} after conversion")
                    
        except Exception as e:
            logger.error(f"💥 Error writing audio for user {self.username}: {e}", exc_info=True)
    
    def pad_to_sample(self, target_sample: int):
        """Pad with silence to reach the target sample position."""
        logger.debug(f"pad_to_sample called for {self.username}: target={target_sample}, current={self.sample_cursor}, closed={self.is_closed}")
        
        if self.is_closed:
            logger.warning(f"Cannot pad {self.username}: audio buffer is closed")
            return
            
        with self.lock:
            if target_sample > self.sample_cursor:
                missing_samples = target_sample - self.sample_cursor
                logger.debug(f"About to write {missing_samples} silence samples for {self.username}")
                
                # Limit excessive padding (more than 5 minutes for safety)
                max_padding = 5 * 60 * 48000  # 5 minutes at 48kHz
                if missing_samples > max_padding:
                    logger.warning(f"Excessive padding requested for {self.username}: {missing_samples} samples ({missing_samples/48000:.1f}s), limiting to {max_padding}")
                    missing_samples = max_padding
                
                self.write_silence(missing_samples)
                logger.debug(f"✅ Padded {missing_samples} samples to reach position {target_sample} for user {self.username}")
            else:
                logger.debug(f"No padding needed for {self.username}: target={target_sample} <= current={self.sample_cursor}")
    
    def get_current_sample(self) -> int:
        """Get current sample position."""
        with self.lock:
            return self.sample_cursor
    
    def close(self):
        """Close and export the audio buffer to MP3 file."""
        with self.lock:
            if not self.is_closed and len(self.audio_buffer) > 0:
                try:
                    # Create AudioSegment from PCM buffer
                    audio_segment = AudioSegment(
                        bytes(self.audio_buffer),
                        sample_width=2,  # 16-bit
                        frame_rate=SAMPLERATE,  # 48000 Hz
                        channels=1  # mono
                    )
                    
                    # Export to MP3
                    audio_segment.export(self.file_path, format="mp3")
                    logger.debug(f"UserWavWriter exported MP3 for {self.username}: {self.sample_cursor} samples written to {self.file_path}")
                    
                except Exception as e:
                    logger.error(f"Error exporting MP3 for {self.username}: {e}", exc_info=True)
                
                self.is_closed = True
                # Clear buffer to free memory
                self.audio_buffer.clear()
            elif not self.is_closed:
                logger.debug(f"UserWavWriter closed for {self.username}: no audio data to export")
                self.is_closed = True


class AlignedPerUserSink(voice_recv.AudioSink):
    """AudioSink that creates time-aligned MP3 files per user and handles STT."""
    
    def __init__(self, output_dir: str, garmin_manager=None):
        super().__init__()
        self.output_dir = output_dir
        self.garmin_manager = garmin_manager  # Reference to call STT callback
        self.session_start_time = time.perf_counter()
        self.session_start_timestamp = datetime.now().strftime('%d.%m.%y_%H-%M-%S')
        self.user_writers: Dict[int, UserWavWriter] = {}
        self.writers_lock = threading.Lock()
        self.is_recording = True
        self.total_session_samples = 0
        
        # Frame timing constants
        self.samples_per_ms = SAMPLERATE / 1000.0  # 48 samples per ms
        self.expected_frame_samples = int(20 * self.samples_per_ms)  # 20ms = 960 samples
        
        # Timeline tracking for gap detection
        self.last_frame_times: Dict[int, float] = {}  # user_id -> last frame time
        self.user_sample_cursors: Dict[int, int] = {}  # user_id -> expected next sample position
        
        # Post-processing silence compression settings (always enabled for optimal performance)
        self.silence_compression_threshold = 5.0  # Compress silence longer than 8 seconds  
        self.silence_compression_target = 1.0    # Reduce long silence to 1 second
        
        # Cleanup state tracking
        self.cleanup_completed = False
        
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"AlignedPerUserSink initialized: session_start={self.session_start_timestamp}")
    
    def wants_opus(self) -> bool:
        """Request PCM data instead of Opus."""
        return False
    
    def write(self, user: Optional[discord.User], data: voice_recv.VoiceData):
        """Process incoming audio data with time alignment and STT."""
        if not self.is_recording:
            logger.debug("AlignedPerUserSink: Not recording, ignoring write")
            return
        
        # Forward to STT callback if available
        if self.garmin_manager and hasattr(self.garmin_manager, 'callback'):
            try:
                self.garmin_manager.callback(user, data)
            except Exception as e:
                logger.error(f"Error in STT callback: {e}")
        
        # Handle unknown users
        if user is None:
            user_id = 0
            username = "unknown"
        else:
            user_id = user.id
            username = sanitize_filename(user.name)
        
        # Check if we have PCM data
        if not data.pcm:
            return
        
        current_time = time.perf_counter()
        session_elapsed_ms = (current_time - self.session_start_time) * 1000
        expected_sample_position = int(session_elapsed_ms * self.samples_per_ms)
        
        # Get or create writer for this user
        writer = self._get_or_create_writer(user_id, username)
        if not writer:
            logger.error(f"Failed to create writer for user {username}")
            return
        
        # Calculate frame duration and samples
        frame_duration_ms = self._estimate_frame_duration(data)
        frame_samples = int(frame_duration_ms * self.samples_per_ms)
        
        # Gap detection and padding
        self._handle_gaps_and_padding(user_id, writer, expected_sample_position, frame_samples, current_time)
        
        # Write the actual audio data
        if data.pcm:
            writer.write_audio(data.pcm)
            
            # Single consolidated log line with all important info
            logger.debug(f"🎤 {username}: {len(data.pcm)}B → {writer.get_current_sample()} samples ({session_elapsed_ms:.1f}ms)")
        else:
            logger.warning(f"No PCM data in voice data for user {username}")
        
        # Update tracking
        self.last_frame_times[user_id] = current_time
        self.user_sample_cursors[user_id] = writer.get_current_sample()
        
        # Update total session length
        self.total_session_samples = max(self.total_session_samples, expected_sample_position + frame_samples)
    
    def _get_or_create_writer(self, user_id: int, username: str) -> Optional[UserWavWriter]:
        """Get existing writer or create new one for user."""
        with self.writers_lock:
            if user_id in self.user_writers:
                return self.user_writers[user_id]
            
            # Create filename: dd.mm.yy_HH-MM-SS_username.mp3
            filename = f"{self.session_start_timestamp}_{username}.mp3"
            file_path = os.path.join(self.output_dir, filename)
            
            try:
                writer = UserWavWriter(file_path, user_id, username)
                self.user_writers[user_id] = writer
                
                # Pad from session start to current position (CRITICAL for time alignment)
                current_time = time.perf_counter()
                session_elapsed_ms = (current_time - self.session_start_time) * 1000
                current_sample_position = int(session_elapsed_ms * self.samples_per_ms)
                logger.debug(f"About to pad writer for {username} to sample {current_sample_position} (session elapsed: {session_elapsed_ms:.1f}ms)")
                writer.pad_to_sample(current_sample_position)
                logger.debug(f"Initial padding completed for {username}")
                
                logger.info(f"Created aligned writer for user {username} (ID: {user_id}): {file_path}")
                return writer
            except Exception as e:
                logger.error(f"Failed to create writer for user {username} (ID: {user_id}): {e}")
                return None
    
    def _estimate_frame_duration(self, data: voice_recv.VoiceData) -> float:
        """Estimate frame duration in milliseconds from VoiceData."""
        # Default Discord frame is 20ms, but can vary
        if hasattr(data, 'duration') and data.duration:
            return data.duration
        
        # Fallback: estimate from PCM data length
        if data.pcm:
            # Assuming stereo 16-bit PCM at 48kHz
            samples = len(data.pcm) // 4  # stereo 16-bit
            duration_ms = (samples / SAMPLERATE) * 1000
            return duration_ms
        
        # Default fallback
        return 20.0  # 20ms default
    
    def _handle_gaps_and_padding(self, user_id: int, writer: UserWavWriter, 
                                  expected_position: int, frame_samples: int, current_time: float):
        """Handle gap detection and silence padding."""
        # For first frame from this user, just update cursor
        if user_id not in self.user_sample_cursors:
            self.user_sample_cursors[user_id] = expected_position
            return
        
        current_cursor = writer.get_current_sample()
        
        # Check for gap based on time elapsed
        if user_id in self.last_frame_times:
            time_since_last = current_time - self.last_frame_times[user_id]
            expected_samples_since_last = int(time_since_last * 1000 * self.samples_per_ms)
            
            # If gap is detected (more than 1.5x expected frame duration)
            if expected_samples_since_last > (self.expected_frame_samples * 1.5):
                gap_samples = expected_position - current_cursor
                if gap_samples > 0:
                    writer.write_silence(gap_samples)
                    # Only log very significant gaps  
                    if gap_samples > 240000:  # More than 5 seconds
                        logger.debug(f"🔇 Gap filled for {writer.username}: {gap_samples} samples ({gap_samples/48000:.1f}s)")
    
    def _copy_to_garmin_output(self, compressed_files: dict, timeline_data: dict, is_compressed: bool = True):
        """Copy compressed files to garmin-output directory with WebGUI naming scheme."""
        import shutil
        
        # Create garmin-output directory if it doesn't exist
        garmin_output_dir = os.path.join(os.path.dirname(self.output_dir), "garmin-output")
        os.makedirs(garmin_output_dir, exist_ok=True)
        
        # Parse session timestamp for filename
        session_timestamp = self.session_start_timestamp  # Format: 22.08.25_15-15-30
        try:
            # Convert from 22.08.25_15-15-30 to dd.mm.yy_hh-mm-ss format
            date_part = session_timestamp.split('_')[0]  # 22.08.25
            time_part = session_timestamp.split('_')[1]  # 15-15-30
            
            # Parse date: 22.08.25 -> 22.08.25 (already correct)
            # Parse time: 15-15-30 -> 15-15-30 (already correct)
            
            webgui_timestamp = f"{date_part}_{time_part}"
            
        except Exception as e:
            logger.warning(f"Error parsing timestamp {session_timestamp}, using fallback: {e}")
            # Fallback to current time
            now = datetime.now()
            webgui_timestamp = now.strftime("%d.%m.%y_%H-%M-%S")
        
        logger.info(f"📂 Copying {'compressed' if is_compressed else 'uncompressed'} files to garmin-output with timestamp: {webgui_timestamp}")
        
        copied_files = []
        
        for file_type, source_file in compressed_files.items():
            if not os.path.exists(source_file):
                logger.warning(f"Source file not found: {source_file}")
                continue
                
            try:
                if file_type == 'mixed':
                    # Mixed file: {dd.mm.yy_hh-mm-ss}.mp3
                    dest_filename = f"{webgui_timestamp}.mp3"
                else:
                    # Individual user file: {dd.mm.yy_hh-mm-ss}_user_{username}.mp3
                    # Extract username from original filename
                    original_basename = os.path.basename(source_file)
                    
                    if is_compressed and '_compressed.mp3' in original_basename:
                        # Find user ID in timeline to get username for compressed files
                        user_id = None
                        for uid, user_data in timeline_data.get('users', {}).items():
                            if user_data['file_path'] == source_file.replace('_compressed.mp3', '.mp3'):
                                username = user_data['username']
                                dest_filename = f"{webgui_timestamp}_user_{username}.mp3"
                                break
                        else:
                            # Fallback: extract from source filename
                            parts = original_basename.split('_')
                            if len(parts) >= 4:
                                username = parts[-1].replace('_compressed.mp3', '').replace('.mp3', '')
                                dest_filename = f"{webgui_timestamp}_user_{username}.mp3"
                            else:
                                logger.warning(f"Cannot determine username for {source_file}")
                                continue
                    else:
                        # For uncompressed files, extract username from the original filename
                        # Original filename format: dd.mm.yy_HH-MM-SS_username.mp3
                        original_basename = os.path.basename(source_file)
                        if '_' in original_basename:
                            # Split by underscore and get the username part
                            parts = original_basename.split('_')
                            if len(parts) >= 3:
                                # Format: dd.mm.yy_HH-MM-SS_username.mp3
                                username = parts[-1].replace('.mp3', '')
                                dest_filename = f"{webgui_timestamp}_user_{username}.mp3"
                            else:
                                logger.warning(f"Cannot determine username for uncompressed file: {source_file}")
                                continue
                        else:
                            logger.warning(f"Unexpected filename format for uncompressed file: {source_file}")
                            continue
                
                dest_path = os.path.join(garmin_output_dir, dest_filename)
                
                # Copy file
                shutil.copy2(source_file, dest_path)
                copied_files.append(dest_filename)
                
                logger.info(f"📄 Copied: {os.path.basename(source_file)} → {dest_filename}")
                
            except Exception as e:
                logger.error(f"Error copying {source_file}: {e}", exc_info=True)
        
        if copied_files:
            logger.info(f"✅ Successfully copied {len(copied_files)} files to garmin-output for WebGUI")
        else:
            logger.warning("⚠️ No files were copied to garmin-output")
    
    def _load_audio_parallel(self, wav_files: List[str]) -> tuple[Dict[str, AudioSegment], int]:
        """Load multiple audio files in parallel for faster processing with MP3 optimization."""
        try:
            start_time = time.perf_counter()
            logger.debug(f"🚀 Starting parallel loading of {len(wav_files)} MP3 files with optimization...")
            
            # Filter out non-existent files
            valid_files = [f for f in wav_files if os.path.exists(f)]
            if not valid_files:
                logger.warning("⚠️ No valid audio files found for loading")
                return {}, 0
            
            audio_segments = {}
            max_length_ms = 0
            
            def load_audio_optimized(wav_file: str) -> tuple[str, AudioSegment]:
                """Load MP3 audio file with optimized loading."""
                try:
                    # Since we only use MP3 files, use optimized MP3 loading
                    audio = AudioSegment.from_mp3(wav_file)
                    logger.debug(f"🎵 Loaded MP3: {os.path.basename(wav_file)} ({len(audio)}ms)")
                    return wav_file, audio
                    
                except Exception as e:
                    logger.error(f"❌ Failed to load {wav_file}: {e}")
                    raise
            
            # Use global thread pool for parallel loading
            global _audio_thread_pool
            if _audio_thread_pool is None:
                _audio_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
            
            try:
                # Submit all loading tasks with optimized loading
                future_to_file = {
                    _audio_thread_pool.submit(load_audio_optimized, wav_file): wav_file 
                    for wav_file in valid_files
                }
                
                # Process completed tasks as they finish
                for future in concurrent.futures.as_completed(future_to_file):
                    wav_file = future_to_file[future]
                    try:
                        wav_file, audio = future.result()
                        audio_segments[wav_file] = audio
                        max_length_ms = max(max_length_ms, len(audio))
                    except Exception as e:
                        logger.error(f"❌ Failed to load {wav_file}: {e}")
            except RuntimeError as e:
                if "cannot schedule new futures after interpreter shutdown" in str(e):
                    logger.warning("⚠️ Interpreter shutting down, using sequential loading")
                    # Fallback to sequential loading during shutdown
                    for wav_file in valid_files:
                        try:
                            wav_file, audio = load_audio_optimized(wav_file)
                            audio_segments[wav_file] = audio
                            max_length_ms = max(max_length_ms, len(audio))
                        except Exception as load_error:
                            logger.error(f"❌ Failed to load {wav_file}: {load_error}")
                else:
                    raise
            
            load_time = time.perf_counter() - start_time
            logger.info(f"🚀 Parallel loading completed in {load_time:.2f}s: {len(audio_segments)} files loaded")
            
            return audio_segments, max_length_ms
            
        except Exception as e:
            logger.error(f"❌ Error in parallel audio loading: {e}", exc_info=True)
            return {}, 0
    
    def _process_audio_in_chunks(self, audio: AudioSegment, chunk_size_ms: int = None) -> List[AudioSegment]:
        """Process large audio files in chunks to optimize memory usage."""
        if chunk_size_ms is None:
            chunk_size_ms = AUDIO_CHUNK_SIZE_MS
        
        chunks = []
        total_length = len(audio)
        
        for start_ms in range(0, total_length, chunk_size_ms):
            end_ms = min(start_ms + chunk_size_ms, total_length)
            chunk = audio[start_ms:end_ms]
            chunks.append(chunk)
        
        logger.debug(f"🎵 Split audio into {len(chunks)} chunks of ~{chunk_size_ms}ms each")
        return chunks
    
    def _detect_silence_advanced(self, audio: AudioSegment, chunk_size_ms: int) -> List[dict]:
        """Advanced silence detection using Voice Activity Detection and spectral analysis.
        
        OPTIMIZATION #5: Advanced Silence Detection
        - Voice Activity Detection (VAD) instead of simple volume threshold
        - Spectral energy analysis for better accuracy
        - Adaptive thresholds based on audio content
        - Configurable sensitivity for different environments
        """
        try:
            silence_periods = []
            total_length = len(audio)
            
            # Convert audio to numpy array for analysis
            # Get audio samples as numpy array
            samples = np.array(audio.get_array_of_samples())
            sample_rate = audio.frame_rate
            
            logger.debug(f"🔍 Advanced silence detection: analyzing {total_length}ms audio with {len(samples)} samples")
            
            # Process audio in chunks for analysis
            for chunk_start in range(0, total_length, chunk_size_ms):
                chunk_end = min(chunk_start + chunk_size_ms, total_length)
                
                # Convert chunk time to sample indices
                start_sample = int(chunk_start * sample_rate / 1000)
                end_sample = int(chunk_end * sample_rate / 1000)
                chunk_samples = samples[start_sample:end_sample]
                
                if len(chunk_samples) == 0:
                    continue
                
                # Advanced silence detection using multiple methods
                is_silent = self._analyze_chunk_silence(chunk_samples, sample_rate)
                
                # Track silence periods
                if is_silent:
                    if not silence_periods or silence_periods[-1]['end'] != chunk_start:
                        # New silence period
                        silence_periods.append({'start': chunk_start, 'end': chunk_end})
                    else:
                        # Extend current silence period
                        silence_periods[-1]['end'] = chunk_end
            
            logger.debug(f"🔍 Advanced detection found {len(silence_periods)} potential silence periods")
            
            return silence_periods
            
        except Exception as e:
            logger.error(f"❌ Error in advanced silence detection: {e}")
            logger.warning("🔄 Advanced silence detection failed, returning empty result")
            return []
    
    def _analyze_chunk_silence(self, chunk: np.ndarray, sample_rate: int) -> bool:
        """Analyze a single audio chunk for silence using multiple criteria."""
        try:
            # 1. Voice Activity Detection (VAD) - Energy-based
            energy = np.mean(chunk ** 2)
            vad_silence = energy < SILENCE_VAD_THRESHOLD
            
            # 2. Spectral Analysis - Frequency domain energy
            if len(chunk) > 0:
                # Compute FFT and get spectral energy
                fft = np.fft.fft(chunk)
                spectral_energy = np.mean(np.abs(fft) ** 2)
                spectral_silence = spectral_energy < SILENCE_SPECTRAL_THRESHOLD
            else:
                spectral_silence = True
            
            # 3. Zero-crossing rate - Simple but effective
            zero_crossings = np.sum(np.diff(np.signbit(chunk).astype(int)))
            zero_crossing_rate = zero_crossings / len(chunk) if len(chunk) > 0 else 0
            zcr_silence = zero_crossing_rate < 0.1  # Low zero-crossing rate indicates silence
            
            # Combined decision: All three must indicate silence
            is_silence = vad_silence and spectral_silence and zcr_silence
            
            return is_silence
            
        except Exception as e:
            logger.error(f"❌ Error in chunk analysis: {e}")
            return False  # Return False (not silence) on error for safety
    

    
    def mixing_audio(self, wav_files: List[str], timeline_data: dict, is_compressed: bool = False) -> dict:
        """Create mixed audio file from multiple user MP3 tracks.
        
        OPTIMIZATION #3: Audio Format Optimization
        - MP3-specific loading (AudioSegment.from_mp3)
        - High-quality MP3 export (192k, -q:a 2)
        - Memory-efficient chunk processing
        """
        try:
            if len(wav_files) <= 1:
                logger.debug("ℹ️ Only one track - no mixing needed")
                return {}
            
            compression_suffix = "_compressed" if is_compressed else ""
            logger.info(f"🎵 Creating mixed file from {len(wav_files)} {'compressed' if is_compressed else 'uncompressed'} recordings...")
            logger.info(f"📁 Input files: {[os.path.basename(f) for f in wav_files]}")
            
            # Load all audio files in parallel (OPTIMIZATION #1)
            audio_segments, max_length_ms = self._load_audio_parallel(wav_files)
            
            if not audio_segments:
                logger.warning("⚠️ No audio files were loaded successfully")
                return {}
            
            # Create mixed file from all tracks
            if len(audio_segments) > 1:
                # Start with silent base track
                mixed_audio = None
                
                for wav_file, audio in audio_segments.items():
                    if mixed_audio is None:
                        # First track becomes the base
                        mixed_audio = audio
                        logger.debug(f"Using {os.path.basename(wav_file)} as base track")
                    else:
                        # Overlay additional tracks
                        mixed_audio = mixed_audio.overlay(audio)
                        logger.debug(f"Overlaid {os.path.basename(wav_file)} onto mix")
                
                # Save mixed file
                if mixed_audio:
                    # Create mixed filename based on session timestamp
                    session_timestamp = os.path.basename(list(wav_files)[0]).split('_')[0] + '_' + os.path.basename(list(wav_files)[0]).split('_')[1]
                    mixed_file = os.path.join(os.path.dirname(list(wav_files)[0]), f"{session_timestamp}_mixed{compression_suffix}.mp3")
                    
                    # OPTIMIZATION #3: Use optimized MP3 export settings to preserve quality
                    mixed_audio.export(
                        mixed_file, 
                        format="mp3",
                        bitrate=AUDIO_EXPORT_BITRATE,
                        parameters=AUDIO_EXPORT_QUALITY
                    )
                    
                    # Create result dictionary
                    result_files = {}
                    for wav_file in wav_files:
                        result_files[wav_file] = wav_file
                    result_files['mixed'] = mixed_file
                    
                    logger.info(f"✅ Mixed file created: {os.path.basename(mixed_file)} ({len(mixed_audio)}ms, {len(audio_segments)} tracks)")
                    logger.info(f"📋 Added mixed file to result list: {os.path.basename(mixed_file)}")
                    
                    return result_files
                else:
                    logger.warning("⚠️ Failed to create mixed audio")
                    return {}
            else:
                logger.debug("ℹ️ Only one track - no mixing needed")
                return {}
                
        except Exception as e:
            logger.error(f"❌ Error in mixing_audio: {e}", exc_info=True)
            return {}
    
    def compress_recordings_post_process(self, wav_files: List[str], timeline_data: dict) -> dict:
        """Post-process MP3 files to remove long silence gaps while maintaining sync.
        
        OPTIMIZATION #3: Audio Format Optimization
        - MP3-specific loading (AudioSegment.from_mp3)
        - High-quality MP3 export (192k, -q:a 2)
        - Memory-efficient chunk processing
        
        OPTIMIZATION #5: Advanced Silence Detection
        - Voice Activity Detection (VAD) instead of simple volume threshold
        - Spectral energy analysis for better accuracy
        - Adaptive thresholds based on audio content
        - Configurable sensitivity for different environments
        """
        try:
            logger.info(f"🔧 Starting post-process compression of {len(wav_files)} files")
            
            # Load all audio files in parallel (OPTIMIZATION #1)
            audio_segments, max_length_ms = self._load_audio_parallel(wav_files)
            
            if not audio_segments:
                logger.warning("⚠️ No audio files were loaded successfully for compression")
                return None
                
            logger.debug(f"Original session length: {max_length_ms}ms")
            
            # Find silence periods across ALL tracks
            silence_threshold_ms = int(self.silence_compression_threshold * 1000)
            silence_target_ms = int(self.silence_compression_target * 1000)
            
            # OPTIMIZATION #5: Advanced silence detection (VAD + Spectral Analysis)
            chunk_size_ms = 750  # Optimal chunk size for analysis
            logger.info("🔍 Using ADVANCED silence detection (VAD + Spectral Analysis)")
            
            # Use advanced silence detection on the first audio track as reference
            # (all tracks should have similar silence patterns in Discord recordings)
            reference_audio = list(audio_segments.values())[0]
            silence_periods = self._detect_silence_advanced(reference_audio, chunk_size_ms)
            
            # Filter silence periods to only include those long enough to compress
            filtered_periods = []
            for period in silence_periods:
                duration_ms = period['end'] - period['start']
                if duration_ms >= SILENCE_MIN_DURATION_MS:
                    filtered_periods.append(period)
            
            silence_periods = filtered_periods
            logger.info(f"🔍 Advanced detection found {len(silence_periods)} silence periods (min duration: {SILENCE_MIN_DURATION_MS}ms)")
            
            # Identify long silence periods to compress
            compressions = []
            total_saved_ms = 0
            
            for period in silence_periods:
                duration_ms = period['end'] - period['start']
                if duration_ms > silence_threshold_ms:
                    # Compress this period
                    saved_ms = duration_ms - silence_target_ms
                    compressions.append({
                        'original_start': period['start'],
                        'original_end': period['end'],
                        'compressed_duration': silence_target_ms,
                        'saved_ms': saved_ms
                    })
                    total_saved_ms += saved_ms
                    
            logger.info(f"Found {len(compressions)} silence periods to compress, saving {total_saved_ms}ms total")
            
            # Apply compressions to all audio files
            compressed_files = {}
            compressed_audio_segments = {}
            
            for wav_file, audio in audio_segments.items():
                compressed_audio = AudioSegment.empty()
                current_pos = 0
                
                for compression in compressions:
                    # Add audio before compression
                    if current_pos < compression['original_start']:
                        compressed_audio += audio[current_pos:compression['original_start']]
                    
                    # Add compressed silence
                    compressed_audio += AudioSegment.silent(duration=compression['compressed_duration'])
                    
                    current_pos = compression['original_end']
                
                # Add remaining audio
                if current_pos < len(audio):
                    compressed_audio += audio[current_pos:]
                
                # Save compressed file
                compressed_file = wav_file.replace('.mp3', '_compressed.mp3')
                # OPTIMIZATION #3: Use optimized MP3 export settings to preserve quality
                compressed_audio.export(
                    compressed_file, 
                    format="mp3",
                    bitrate=AUDIO_EXPORT_BITRATE,
                    parameters=AUDIO_EXPORT_QUALITY
                )
                compressed_files[wav_file] = compressed_file
                compressed_audio_segments[wav_file] = compressed_audio
                
                logger.debug(f"Compressed {wav_file}: {len(audio)}ms -> {len(compressed_audio)}ms")
            
            # Create mixed file from all compressed tracks
            if len(compressed_audio_segments) > 1:
                logger.info("🎵 Creating mixed file from compressed recordings...")
                
                # Use the new mixing function with COMPRESSED file paths
                compressed_file_paths = list(compressed_files.values())
                mixed_result = self.mixing_audio(compressed_file_paths, timeline_data, is_compressed=True)
                if mixed_result and 'mixed' in mixed_result:
                    compressed_files['mixed'] = mixed_result['mixed']
                    logger.info(f"✅ Mixed compressed file created via mixing_audio function")
                else:
                    logger.warning("⚠️ Failed to create mixed compressed file")
            else:
                logger.debug("ℹ️ Only one track - no mixed file needed")
            
            # Update timeline data
            compressed_timeline = timeline_data.copy()
            compressed_timeline['original_duration_ms'] = timeline_data['session_duration_ms']
            compressed_timeline['session_duration_ms'] = max_length_ms - total_saved_ms
            compressed_timeline['compressed_silence_ms'] = total_saved_ms
            compressed_timeline['compression_method'] = 'post_process'
            compressed_timeline['compressions'] = compressions
            
            # Add mixed file info if created
            if 'mixed' in compressed_files:
                compressed_timeline['mixed_file'] = {
                    'file_path': compressed_files['mixed'],
                    'tracks_count': len([f for f in compressed_files if f != 'mixed']),
                    'description': 'All compressed user tracks overlaid synchronously'
                }
            
            logger.info(f"✅ Post-processing complete: {max_length_ms}ms -> {max_length_ms - total_saved_ms}ms")
            
            return {
                'compressed_files': compressed_files,
                'timeline': compressed_timeline,
                'saved_ms': total_saved_ms
            }
            
        except Exception as e:
            logger.error(f"Error in post-process compression: {e}", exc_info=True)
            return None
    
    def cleanup(self):
        """Cleanup: pad all writers to session end and close files."""
        # Prevent double cleanup
        if self.cleanup_completed:
            logger.debug(f"🔄 Cleanup already completed for session {self.session_start_timestamp}, skipping duplicate execution")
            return
            
        logger.info(f"AlignedPerUserSink cleanup: finalizing aligned recordings for session {self.session_start_timestamp}")
        
        try:
            self.is_recording = False
            
            # Calculate final session length
            current_time = time.perf_counter()
            session_duration_ms = (current_time - self.session_start_time) * 1000
            final_session_samples = int(session_duration_ms * self.samples_per_ms)
            
            logger.debug(f"Session duration: {session_duration_ms:.1f}ms, final samples: {final_session_samples}")
            
            # Update total if larger
            self.total_session_samples = max(self.total_session_samples, final_session_samples)
            
            # Pad all writers to session end and close
            with self.writers_lock:
                logger.debug(f"Processing {len(self.user_writers)} user writers")
                
                timeline_data = {
                    'session_start': self.session_start_timestamp,
                    'session_duration_ms': session_duration_ms,
                    'session_samples': self.total_session_samples,
                    'sample_rate': SAMPLERATE,
                    'users': {},
                    'compression': {
                        'enabled': False,
                        'original_duration_ms': session_duration_ms,
                        'compressed_duration_ms': session_duration_ms,
                        'silence_saved_ms': 0,
                        'method': 'none'
                    }
                }
                
                for user_id, writer in self.user_writers.items():
                    try:
                        logger.debug(f"Processing user {writer.username} (ID: {user_id})")
                        current_samples = writer.get_current_sample()
                        
                        # Only pad if needed (avoid excessive padding)
                        if current_samples < self.total_session_samples:
                            samples_to_pad = self.total_session_samples - current_samples
                            logger.debug(f"Padding {samples_to_pad} samples for {writer.username}")
                            
                            # Check if writer is still valid before padding
                            if not writer.is_closed:
                                writer.pad_to_sample(self.total_session_samples)
                            else:
                                logger.warning(f"Writer for {writer.username} is already closed, skipping padding")
                        
                        # Record timeline info
                        timeline_data['users'][str(user_id)] = {  # Ensure string key for JSON
                            'username': writer.username,
                            'file_path': writer.file_path,
                            'final_samples': writer.get_current_sample()
                        }
                        
                        # Close writer
                        writer.close()
                        logger.info(f"✅ Finalized aligned recording for {writer.username}: {writer.get_current_sample()} samples")
                        
                    except Exception as e:
                        logger.error(f"❌ Error finalizing writer for user {user_id}: {e}", exc_info=True)
                        # Try to close writer anyway
                        try:
                            writer.close()
                        except:
                            pass
                
                # Save timeline JSON
                timeline_path = os.path.join(self.output_dir, f"timeline_{self.session_start_timestamp}.json")
                try:
                    logger.debug(f"Saving timeline to: {timeline_path}")
                    os.makedirs(os.path.dirname(timeline_path), exist_ok=True)
                    
                    with open(timeline_path, 'w', encoding='utf-8') as f:
                        json.dump(timeline_data, f, indent=2, ensure_ascii=False)
                    logger.info(f"✅ Saved timeline data: {timeline_path}")
                    
                    # POST-PROCESSING: Compress silence if enabled
                    try:
                        wav_files = [data['file_path'] for data in timeline_data['users'].values()]
                        
                        logger.info(f"🔧 Silence compression status: always enabled, threshold={self.silence_compression_threshold}s")
                        
                        if len(wav_files) > 0:
                            logger.info("🔧 Starting post-process silence compression...")
                            
                            compression_result = self.compress_recordings_post_process(wav_files, timeline_data)
                            
                            if compression_result:
                                # UPDATE existing timeline with compression info
                                timeline_data['compression'] = {
                                    'enabled': True,
                                    'original_duration_ms': compression_result['timeline']['original_duration_ms'],
                                    'compressed_duration_ms': compression_result['timeline']['session_duration_ms'],
                                    'silence_saved_ms': compression_result['saved_ms'],
                                    'method': 'post_process'
                                }
                                
                                # Add compressed file info
                                if 'mixed' in compression_result['compressed_files']:
                                    timeline_data['mixed_file'] = {
                                        'file_path': compression_result['compressed_files']['mixed'],
                                        'tracks_count': len([f for f in compression_result['compressed_files'] if f != 'mixed']),
                                        'description': 'All compressed user tracks overlaid synchronously'
                                    }
                                
                                # Add compressed file paths to user data
                                for user_id_str, user_data in timeline_data['users'].items():
                                    original_path = user_data['file_path']
                                    compressed_path = original_path.replace('.mp3', '_compressed.mp3')
                                    if compressed_path in compression_result['compressed_files'].values():
                                        user_data['compressed_file_path'] = compressed_path
                                
                                # OVERWRITE the same timeline file with updated data
                                with open(timeline_path, 'w', encoding='utf-8') as f:
                                    json.dump(timeline_data, f, indent=2, ensure_ascii=False)
                                
                                logger.info(f"✅ Compressed recordings saved: {compression_result['saved_ms']}ms saved")
                                logger.info(f"✅ Timeline updated with compression info")
                                
                                # Copy compressed files to garmin-output with WebGUI naming
                                try:
                                    self._copy_to_garmin_output(compression_result['compressed_files'], timeline_data, is_compressed=True)
                                except Exception as copy_error:
                                    logger.error(f"❌ Error copying to garmin-output: {copy_error}", exc_info=True)
                                
                                # Log compression details
                                for file_type, compressed in compression_result['compressed_files'].items():
                                    if file_type == 'mixed':
                                        logger.info(f"   🎵 {os.path.basename(compressed)} (mixed)")
                                    else:
                                        logger.info(f"   📄 {os.path.basename(compressed)}")
                            else:
                                logger.warning("⚠️ Post-process compression failed")
                        else:
                            logger.debug("ℹ️ Skipping post-process compression (disabled or no files)")
                            
                            # IMPORTANT: Even when compression is disabled, we need to copy files to garmin-output
                            if len(wav_files) > 0:
                                logger.info("📂 Copying uncompressed files to garmin-output...")
                                try:
                                    # Create a simple file mapping for uncompressed files
                                    uncompressed_files = {}
                                    for wav_file in wav_files:
                                        # For uncompressed files, we'll copy the original files
                                        # Use the file path as both key and value for uncompressed files
                                        uncompressed_files[wav_file] = wav_file
                                    
                                    # Create mixed file from uncompressed tracks (even when compression is disabled)
                                    if len(wav_files) > 1:
                                        logger.info("🎵 Creating mixed file from uncompressed recordings...")
                                        
                                        # Use the new mixing function
                                        mixed_result = self.mixing_audio(wav_files, timeline_data, is_compressed=False)
                                        if mixed_result and 'mixed' in mixed_result:
                                            uncompressed_files['mixed'] = mixed_result['mixed']
                                            logger.info(f"✅ Mixed uncompressed file created via mixing_audio function")
                                        else:
                                            logger.warning("⚠️ Failed to create mixed uncompressed file")
                                    else:
                                        logger.debug("ℹ️ Only one track - no mixing needed")
                                    
                                    # Copy uncompressed files to garmin-output
                                    self._copy_to_garmin_output(uncompressed_files, timeline_data, is_compressed=False)
                                    logger.info("✅ Uncompressed files copied to garmin-output")
                                except Exception as copy_error:
                                    logger.error(f"❌ Error copying uncompressed files to garmin-output: {copy_error}", exc_info=True)
                    
                    except Exception as comp_error:
                        logger.error(f"❌ Error in post-process compression: {comp_error}", exc_info=True)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to save timeline data: {e}", exc_info=True)
            
            logger.info(f"🎯 AlignedPerUserSink cleanup completed: {len(self.user_writers)} user tracks, "
                       f"{self.total_session_samples} samples ({session_duration_ms:.1f}ms)")
            
            # Mark cleanup as completed to prevent double execution
            self.cleanup_completed = True
            logger.debug(f"🔒 Cleanup marked as completed for session {self.session_start_timestamp}")
                       
        except Exception as e:
            logger.error(f"💥 Critical error in cleanup: {e}", exc_info=True)
            # Mark cleanup as completed even on error to prevent infinite retries
            self.cleanup_completed = True
            logger.debug(f"🔒 Cleanup marked as completed (with error) for session {self.session_start_timestamp}")


class UserAudioBuffer:
    """Manages audio buffer for a single user."""
    
    def __init__(self, user_id: int, username: str):
        self.user_id = user_id
        self.username = username
        self.audio_segments = []  # List of pydub AudioSegments
        self.raw_buffer = bytearray()  # Raw PCM data buffer
        self.lock = threading.Lock()
        self.last_activity = time.time()
        self.total_duration_ms = 0
        
    def add_audio(self, pcm_data: bytes):
        """Add PCM audio data to the buffer."""
        with self.lock:
            self.raw_buffer.extend(pcm_data)
            self.last_activity = time.time()
            
            # Convert accumulated raw buffer to AudioSegment periodically
            # Process in chunks of 1 second to avoid memory issues
            chunk_size = SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE  # 1 second
            
            while len(self.raw_buffer) >= chunk_size:
                chunk_data = bytes(self.raw_buffer[:chunk_size])
                self.raw_buffer = self.raw_buffer[chunk_size:]
                
                # Create AudioSegment from raw PCM data
                try:
                    segment = AudioSegment(
                        chunk_data,
                        sample_width=SAMPLE_WIDTH,
                        frame_rate=SAMPLERATE,
                        channels=CHANNELS
                    )
                    self.audio_segments.append(segment)
                    self.total_duration_ms += len(segment)
                    
                    # Limit buffer size
                    max_duration_ms = MAX_BUFFER_DURATION_S * 1000
                    if self.total_duration_ms > max_duration_ms:
                        # Remove oldest segments
                        while self.audio_segments and self.total_duration_ms > max_duration_ms:
                            removed = self.audio_segments.pop(0)
                            self.total_duration_ms -= len(removed)
                            
                except Exception as e:
                    logger.error(f"Error creating AudioSegment for user {self.user_id}: {e}")
    
    def get_audio_segment(self) -> Optional[AudioSegment]:
        """Get the complete audio segment for this user."""
        with self.lock:
            if not self.audio_segments and not self.raw_buffer:
                return None
            
            segments = list(self.audio_segments)
            
            # Add any remaining raw buffer data
            if len(self.raw_buffer) > 0:
                try:
                    # Pad to frame boundary if needed
                    remaining = len(self.raw_buffer) % (CHANNELS * BYTES_PER_SAMPLE)
                    if remaining:
                        self.raw_buffer.extend(b'\x00' * (CHANNELS * BYTES_PER_SAMPLE - remaining))
                    
                    segment = AudioSegment(
                        bytes(self.raw_buffer),
                        sample_width=SAMPLE_WIDTH,
                        frame_rate=SAMPLERATE,
                        channels=CHANNELS
                    )
                    segments.append(segment)
                except Exception as e:
                    logger.error(f"Error processing remaining buffer for user {self.user_id}: {e}")
            
            if not segments:
                return None
            
            # Combine all segments
            if len(segments) == 1:
                return segments[0]
            else:
                combined = segments[0]
                for seg in segments[1:]:
                    combined += seg
                return combined
    
    def clear(self):
        """Clear the audio buffer."""
        with self.lock:
            self.audio_segments.clear()
            self.raw_buffer.clear()
            self.total_duration_ms = 0
            

class GarminVoiceManager:
    """Voice listener that reacts on trigger phrases and plays sounds.
    
    OPTIMIZATION #6: Batch Processing Optimization
    - Multiple session handling concurrently
    - Resource pooling for audio processing
    - Queue management for multiple users
    - Efficient cleanup and resource management
    - Session prioritization and load balancing
    """
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.user_buffers: Dict[int, UserAudioBuffer] = {}
        self._buffers_lock = threading.Lock()
        
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
        
        # Recording state
        self.recording_start_time: float = time.time()
        self.vc: Optional[voice_recv.VoiceRecvClient] = None
        
        # Aligned recording sink
        self.aligned_sink: Optional[AlignedPerUserSink] = None
        
        # OPTIMIZATION #6: Batch Processing Manager for multiple concurrent sessions
        self.batch_manager: Optional[BatchProcessingManager] = None
        if BATCH_PROCESSING_ENABLED:
            self.batch_manager = BatchProcessingManager()
            logger.info("🚀 Batch processing manager initialized")
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(ALIGNED_RECORDINGS_DIR, exist_ok=True)
        
        # All setup complete
        
        logger.info("GarminVoiceManager initialized with aligned per-user recording")
    
    # ------------------------- Discord voice callbacks -------------------------
    def callback(self, user: Optional[discord.User], data: voice_recv.VoiceData):
        """Callback for incoming audio data."""
        try:
            # Handle user audio buffering
            if user is not None:
                user_id = user.id
                username = user.name
            else:
                # Unknown user
                user_id = 0
                username = "unknown"
            
            # Add to user buffer
            with self._buffers_lock:
                if user_id not in self.user_buffers:
                    self.user_buffers[user_id] = UserAudioBuffer(user_id, username)
                    logger.debug(f"Created audio buffer for user {username} (ID: {user_id})")
                
                self.user_buffers[user_id].add_audio(data.pcm)
            
            # Add to STT buffer for trigger detection
            if cfg.STT_ENABLED:
                with self._stt_buffer_lock:
                    self.stt_buffer.extend(data.pcm)
                    
                    # Limit STT buffer size
                    max_stt_buffer = WINDOW_BYTES_MAX * 2
                    if len(self.stt_buffer) > max_stt_buffer:
                        self.stt_buffer = self.stt_buffer[-max_stt_buffer:]
                
                # Process STT if enough time has passed
                now = time.time()
                if (len(self.stt_buffer) >= WINDOW_BYTES_MIN and 
                    now - self.last_process_time >= PROCESS_INTERVAL_S):
                    
                    try:
                        if not self.stt_queue.full():
                            self.stt_queue.put_nowait(now)
                            self.last_process_time = now
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
    
    # ------------------------------ Recording management ---------------------------
    def save_recording(self):
        """Save current recording - triggers aligned recording finalization."""
        try:
            # If aligned recording is active, finalize it
            if self.aligned_sink:
                try:
                    # Get the session timestamp for filename generation
                    session_timestamp = self.aligned_sink.session_start_timestamp
                    
                    # Cleanup the sink first to get compression info
                    self.aligned_sink.cleanup()
                    logger.info("✅ Aligned recording saved and finalized")
                    
                    # Generate the correct filename based on session start time
                    aligned_filename = f"{session_timestamp}.mp3"
                    
                    # Track last saved file info
                    self.last_saved_filename = aligned_filename
                    self.last_saved_timestamp = time.time()
                    
                    # Store session timestamp for later use in get_recording_info
                    self.last_session_timestamp = session_timestamp
                    
                    # Reset the sink for continued recording
                    self.aligned_sink = AlignedPerUserSink(ALIGNED_RECORDINGS_DIR, garmin_manager=self)
                    
                    # CRITICAL: Update the voice client to listen to the NEW sink
                    if hasattr(self, 'vc') and self.vc:
                        logger.info("🔄 Switching voice client to new aligned sink for continued recording")
                        try:
                            # Stop current listening first
                            self.vc.stop_listening()
                            logger.debug("Stopped current voice listening")
                            
                            # Start listening with new sink
                            self.vc.listen(self.aligned_sink)
                            logger.info("✅ Voice client now listening to new sink - recording continues")
                        except Exception as e:
                            logger.error(f"Failed to switch to new sink: {e}")
                            # Fallback: try to restart listening anyway
                            try:
                                self.vc.listen(self.aligned_sink)
                                logger.info("✅ Fallback: Voice client listening to new sink")
                            except Exception as e2:
                                logger.error(f"Fallback also failed: {e2}")
                    
                    return
                except Exception as e:
                    logger.error(f"Error saving aligned recording: {e}")
            
            # Fallback to old recording method
            timestamp = time.strftime('%d.%m.%y_%H-%M-%S', time.localtime())
            base_filename = f"recording_{timestamp}"
            
            with self._buffers_lock:
                active_users = []
                user_segments = {}
                
                # Collect audio segments from all users
                for user_id, buffer in self.user_buffers.items():
                    segment = buffer.get_audio_segment()
                    if segment and len(segment) > 0:
                        user_segments[user_id] = segment
                        active_users.append(buffer.username)
                        
                        # Save individual user recording
                        user_filename = f"{base_filename}_user_{sanitize_filename(buffer.username)}.mp3"
                        user_path = os.path.join(OUTPUT_DIR, user_filename)
                        
                        try:
                            segment.export(user_path, format="mp3")
                            duration_s = len(segment) / 1000.0
                            logger.info(f"Saved individual recording for {buffer.username}: {user_path} ({duration_s:.1f}s)")
                        except Exception as e:
                            logger.error(f"Error saving individual recording for {buffer.username}: {e}")
                
                # Create mixed recording if we have audio
                if user_segments:
                    mixed_filename = f"{base_filename}.mp3"
                    mixed_path = os.path.join(OUTPUT_DIR, mixed_filename)
                    
                    try:
                        # Mix all user segments using simple overlay
                        if len(user_segments) == 1:
                            mixed_segment = list(user_segments.values())[0]
                        else:
                            # Find the longest segment duration
                            max_length = max(len(seg) for seg in user_segments.values())
                            mixed_segment = AudioSegment.silent(duration=max_length)
                            
                            # Overlay each user's audio onto the mixed track
                            for segment in user_segments.values():
                                mixed_segment = mixed_segment.overlay(segment, position=0)
                        
                        if mixed_segment:
                            # Export mixed audio
                            mixed_segment.export(mixed_path, format="mp3")
                            duration_s = len(mixed_segment) / 1000.0
                            logger.info(f"Saved mixed recording: {mixed_path} ({duration_s:.1f}s, {len(active_users)} users)")
                            
                            # Track last saved file info
                            self.last_saved_filename = mixed_filename
                            self.last_saved_timestamp = time.time()
                        else:
                            logger.warning("No mixed audio generated")
                            
                    except Exception as e:
                        logger.error(f"Error saving mixed recording: {e}")
                
                # Clear buffers after saving
                for buffer in self.user_buffers.values():
                    buffer.clear()
                
                # Clear STT buffer
                with self._stt_buffer_lock:
                    self.stt_buffer.clear()
                
                logger.info(f"Recording saved successfully with {len(active_users)} users")
                
        except Exception as e:
            logger.error(f"Error during save_recording: {e}")
    
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
            
            # Create aligned recording sink with reference to this manager for STT
            self.aligned_sink = AlignedPerUserSink(ALIGNED_RECORDINGS_DIR, garmin_manager=self)
            
            # OPTIMIZATION #6: Register session with batch manager if enabled
            if self.batch_manager:
                session_id = f"voice_{int(time.time())}"
                self.batch_manager.create_session(session_id, ALIGNED_RECORDINGS_DIR)
                logger.info(f"🚀 Session {session_id} registered with batch manager")
            
            # Use the aligned sink directly (it will handle both STT and recording)
            self.vc.listen(self.aligned_sink)
            
            # Start STT worker if enabled
            if cfg.STT_ENABLED:
                self._start_stt_worker()
                logger.info(f"🔊 Joined voice channel '{channel.name}' (STT: {cfg.STT_ENGINE}, Aligned Recording: ON)")
            else:
                logger.info(f"🔊 Joined voice channel '{channel.name}' (STT disabled, Aligned Recording: ON)")
            
            self.recording_start_time = time.time()
            
        except Exception as e:
            logger.error(f"Failed to join voice channel '{channel.name}': {e}")
            if self.vc:
                try:
                    await self.vc.disconnect()
                except:
                    pass
                self.vc = None
            if self.aligned_sink:
                try:
                    self.aligned_sink.cleanup()
                except:
                    pass
                self.aligned_sink = None
            raise
    
    def is_connected(self) -> bool:
        """Check if connected to a voice channel."""
        return self.vc is not None and self.vc.is_connected()
    
    async def leave_channel(self):
        """Disconnect from the current voice channel."""
        try:
            self._stop_stt_worker()
            
            # Cleanup aligned recording
            if self.aligned_sink:
                try:
                    self.aligned_sink.cleanup()
                    logger.info("✅ Aligned recordings finalized")
                except Exception as e:
                    logger.error(f"Error cleaning up aligned sink: {e}")
                finally:
                    self.aligned_sink = None
            
            # OPTIMIZATION #6: Shutdown batch processing manager
            if self.batch_manager:
                try:
                    self.batch_manager.shutdown()
                    logger.info("🛑 Batch processing manager shutdown complete")
                except Exception as e:
                    logger.error(f"Error shutting down batch manager: {e}")
            
            # Cleanup global thread pool
            global _audio_thread_pool
            if _audio_thread_pool:
                try:
                    _audio_thread_pool.shutdown(wait=False)
                    _audio_thread_pool = None
                    logger.info("🛑 Audio thread pool shutdown complete")
                except Exception as e:
                    logger.error(f"Error shutting down thread pool: {e}")
            
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
            if self.aligned_sink:
                self.aligned_sink = None
    
    def get_recording_health(self) -> dict:
        """Get current recording system health status."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time if self.is_connected() else 0
        
        # Get aligned recording info
        aligned_active_users = 0
        aligned_session_samples = 0
        aligned_session_duration_ms = 0
        
        if self.aligned_sink:
            aligned_active_users = len(self.aligned_sink.user_writers)
            aligned_session_samples = self.aligned_sink.total_session_samples
            aligned_session_duration_ms = (current_time - self.aligned_sink.session_start_time) * 1000 if self.aligned_sink.session_start_time else 0
        
        with self._buffers_lock:
            legacy_active_users = len(self.user_buffers)
            total_buffer_size = 0
            
            for buffer in self.user_buffers.values():
                # Estimate buffer size
                segment = buffer.get_audio_segment()
                if segment:
                    # Calculate approximate size in bytes 
                    duration_s = len(segment) / 1000.0
                    total_buffer_size += int(duration_s * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
        
        return {
            'connected': self.is_connected(),
            'recording_duration': recording_duration,
            'buffer_size': total_buffer_size,  # Keep for backward compatibility
            'recording_errors': 0,  # Keep for backward compatibility
            'max_errors': 5,  # Keep for backward compatibility
            'is_processing': self.is_processing,  # Keep for backward compatibility
            'max_recording_duration': cfg.GARMIN_RECORD_SECONDS if hasattr(cfg, 'GARMIN_RECORD_SECONDS') else 600,  # Keep for backward compatibility
            'audio_pipeline_healthy': True,  # Keep for backward compatibility
            'audio_callback_count': 0,  # Keep for backward compatibility
            'audio_callback_errors': 0,  # Keep for backward compatibility
            'time_since_last_audio': 0,  # Keep for backward compatibility
            'audio_callback_rate': 0,  # Keep for backward compatibility
            'last_chunk_size': 0,  # Keep for backward compatibility
            'active_users': max(legacy_active_users, aligned_active_users),  # Keep for backward compatibility
            'total_user_buffer_size': total_buffer_size,  # Keep for backward compatibility
            'aligned_recording_active': self.aligned_sink is not None,  # Keep for backward compatibility
            'aligned_users': aligned_active_users,  # Keep for backward compatibility
            'aligned_session_samples': aligned_session_samples,  # Keep for backward compatibility
            'aligned_session_duration_ms': aligned_session_duration_ms,  # Keep for backward compatibility
            'aligned_recording': {
                'active_users': aligned_active_users,
                'session_samples': aligned_session_samples,
                'session_duration_ms': aligned_session_duration_ms,
                'session_timestamp': self.aligned_sink.session_start_timestamp if self.aligned_sink else None
            },
            'legacy_recording': {
                'active_users': legacy_active_users,
                'total_buffer_size': total_buffer_size
            },
            'stt': {
                'enabled': cfg.STT_ENABLED,
                'engine': cfg.STT_ENGINE,
                'buffer_size': len(self.stt_buffer),
                'worker_running': self.stt_worker_running
            },
            'batch_processing': self.batch_manager.get_session_stats() if self.batch_manager else None
        }
    
    def get_recording_info(self) -> dict:
        """Get detailed recording information including last saved file details."""
        current_time = time.time()
        recording_duration = current_time - self.recording_start_time if self.is_connected() else 0
        
        # Get buffer information
        with self._buffers_lock:
            active_users = len(self.user_buffers)
            total_buffer_size = 0
            
            for buffer in self.user_buffers.values():
                segment = buffer.get_audio_segment()
                if segment:
                    duration_s = len(segment) / 1000.0
                    total_buffer_size += int(duration_s * SAMPLERATE * CHANNELS * BYTES_PER_SAMPLE)
        
        # Get aligned recording info
        aligned_info = {}
        if self.aligned_sink:
            aligned_info = {
                "aligned_active_users": len(self.aligned_sink.user_writers),
                "aligned_session_samples": self.aligned_sink.total_session_samples,
                "aligned_session_duration_ms": (current_time - self.aligned_sink.session_start_time) * 1000 if self.aligned_sink.session_start_time else 0
            }
        
        # Get actual file size and compression info if available
        actual_file_size = 0
        compressed_duration = None
        
        if hasattr(self, 'last_saved_filename') and self.last_saved_filename:
            # Try to get actual file size from the saved file
            try:
                # Check garmin-output directory first (this is where files are copied for WebGUI)
                garmin_output_file_path = os.path.join(OUTPUT_DIR, self.last_saved_filename)
                if os.path.exists(garmin_output_file_path):
                    actual_file_size = os.path.getsize(garmin_output_file_path)
                    logger.debug(f"Found file in garmin-output: {garmin_output_file_path}, size: {actual_file_size}")
                
                # Try to get compression info from timeline file using last session timestamp
                if hasattr(self, 'last_session_timestamp') and self.last_session_timestamp:
                    timeline_path = os.path.join(ALIGNED_RECORDINGS_DIR, f"timeline_{self.last_session_timestamp}.json")
                    if os.path.exists(timeline_path):
                        try:
                            with open(timeline_path, 'r') as f:
                                timeline_data = json.load(f)
                                if 'compression' in timeline_data and timeline_data['compression']['enabled']:
                                    compressed_duration = timeline_data['compression']['compressed_duration_ms'] / 1000.0
                                    logger.debug(f"Found compression info: {compressed_duration}s")
                        except Exception as e:
                            logger.debug(f"Could not read timeline file: {e}")
                
                # Fallback: check aligned recordings directory if not found in garmin-output
                if actual_file_size == 0 and hasattr(self, 'last_session_timestamp') and self.last_session_timestamp:
                    mixed_file_path = os.path.join(ALIGNED_RECORDINGS_DIR, f"{self.last_session_timestamp}.mp3")
                    if os.path.exists(mixed_file_path):
                        actual_file_size = os.path.getsize(mixed_file_path)
                        logger.debug(f"Found file in aligned recordings: {mixed_file_path}, size: {actual_file_size}")
                
                # Additional fallback: try current aligned sink if available
                elif actual_file_size == 0 and hasattr(self, 'aligned_sink') and self.aligned_sink and hasattr(self.aligned_sink, 'session_start_timestamp'):
                    mixed_file_path = os.path.join(ALIGNED_RECORDINGS_DIR, f"{self.aligned_sink.session_start_timestamp}.mp3")
                    if os.path.exists(mixed_file_path):
                        actual_file_size = os.path.getsize(mixed_file_path)
                        logger.debug(f"Found file in current aligned recordings: {mixed_file_path}, size: {actual_file_size}")
                        
            except Exception as e:
                logger.debug(f"Could not get actual file size: {e}")
        
        return {
            "recording_duration": recording_duration,
            "buffer_size": total_buffer_size,
            "actual_file_size": actual_file_size,
            "active_users": active_users,
            "last_saved_filename": getattr(self, 'last_saved_filename', None),
            "last_saved_timestamp": getattr(self, 'last_saved_timestamp', None),
            "compressed_duration": compressed_duration,
            **aligned_info
        }


class BatchProcessingManager:
    """Manages multiple concurrent recording sessions for efficient batch processing.
    
    OPTIMIZATION #6: Batch Processing Optimization
    - Multiple session handling concurrently
    - Resource pooling for audio processing
    - Queue management for multiple users
    - Efficient cleanup and resource management
    - Session prioritization and load balancing
    """
    
    def __init__(self):
        self.active_sessions: Dict[str, AlignedPerUserSink] = {}
        self.session_queue: queue.Queue = queue.Queue(maxsize=SESSION_QUEUE_SIZE)
        self.session_stats: Dict[str, dict] = {}
        self.resource_pool: Dict[str, Any] = {}
        self.last_cleanup: float = time.time()
        self._lock = threading.Lock()
        self._cleanup_thread = None
        self._cleanup_running = False
        
        # Start cleanup thread if batch processing is enabled
        if BATCH_PROCESSING_ENABLED:
            self._start_cleanup_thread()
    
    def _start_cleanup_thread(self):
        """Start background cleanup thread for resource management."""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._cleanup_running = True
            self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
            self._cleanup_thread.start()
            logger.info("🚀 Batch processing cleanup thread started")
    
    def _cleanup_worker(self):
        """Background worker for periodic cleanup and resource management."""
        while self._cleanup_running:
            try:
                time.sleep(BATCH_CLEANUP_INTERVAL)
                self._perform_cleanup()
            except Exception as e:
                logger.error(f"❌ Error in cleanup worker: {e}")
    
    def _perform_cleanup(self):
        """Perform periodic cleanup of completed sessions and resources."""
        try:
            current_time = time.time()
            
            # Use a timeout to prevent deadlocks
            if not self._lock.acquire(timeout=5.0):
                logger.error(f"❌ Timeout acquiring lock for cleanup")
                return
            
            try:
                # Clean up completed sessions
                completed_sessions = []
                for session_id, session in self.active_sessions.items():
                    if not session.is_recording and session.cleanup_completed:
                        completed_sessions.append(session_id)
                
                for session_id in completed_sessions:
                    del self.active_sessions[session_id]
                    if session_id in self.session_stats:
                        del self.session_stats[session_id]
                    logger.debug(f"🧹 Cleaned up completed session: {session_id}")
                
                # Clean up old statistics
                old_stats = []
                for session_id, stats in self.session_stats.items():
                    if current_time - stats.get('last_activity', 0) > BATCH_CLEANUP_INTERVAL * 2:
                        old_stats.append(session_id)
                
                for session_id in old_stats:
                    del self.session_stats[session_id]
                
                # Log cleanup summary
                if completed_sessions or old_stats:
                    logger.info(f"🧹 Batch cleanup: {len(completed_sessions)} sessions, {len(old_stats)} stats cleaned")
            finally:
                self._lock.release()
                    
        except Exception as e:
            logger.error(f"❌ Error during batch cleanup: {e}")
    
    def create_session(self, session_id: str, output_dir: str) -> Optional[AlignedPerUserSink]:
        """Create a new recording session with resource management."""
        try:
            # Use a timeout to prevent deadlocks
            if not self._lock.acquire(timeout=5.0):
                logger.error(f"❌ Timeout acquiring lock for create_session {session_id}")
                return None
            
            try:
                # Check if we can create a new session
                if len(self.active_sessions) >= MAX_CONCURRENT_SESSIONS:
                    # Add to queue if we're at capacity
                    if not self.session_queue.full():
                        self.session_queue.put((session_id, output_dir))
                        logger.info(f"⏳ Session {session_id} queued (max concurrent: {MAX_CONCURRENT_SESSIONS})")
                        return None
                    else:
                        logger.warning(f"⚠️ Session queue full, rejecting {session_id}")
                        return None
                
                # Create new session
                session = AlignedPerUserSink(output_dir)
                self.active_sessions[session_id] = session
                
                # Initialize session statistics
                self.session_stats[session_id] = {
                    'created': time.time(),
                    'last_activity': time.time(),
                    'status': 'active',
                    'output_dir': output_dir
                }
                
                logger.info(f"✅ Session {session_id} created (active: {len(self.active_sessions)}/{MAX_CONCURRENT_SESSIONS})")
                return session
            finally:
                self._lock.release()
                
        except Exception as e:
            logger.error(f"❌ Error creating session {session_id}: {e}")
            return None
    
    def get_session(self, session_id: str) -> Optional[AlignedPerUserSink]:
        """Get an existing session by ID."""
        try:
            # Use a timeout to prevent deadlocks
            if not self._lock.acquire(timeout=5.0):
                logger.error(f"❌ Timeout acquiring lock for get_session {session_id}")
                return None
            
            try:
                session = self.active_sessions.get(session_id)
                if session:
                    # Update activity timestamp
                    if session_id in self.session_stats:
                        self.session_stats[session_id]['last_activity'] = time.time()
                    return session
                return None
            finally:
                self._lock.release()
                
        except Exception as e:
            logger.error(f"❌ Error getting session {session_id}: {e}")
            return None
    
    def close_session(self, session_id: str) -> bool:
        """Close and cleanup a session."""
        try:
            # Use a timeout to prevent deadlocks
            if not self._lock.acquire(timeout=5.0):
                logger.error(f"❌ Timeout acquiring lock for session {session_id}")
                return False
            
            try:
                if session_id in self.active_sessions:
                    session = self.active_sessions[session_id]
                    session.cleanup()
                    
                    # Remove from active sessions
                    del self.active_sessions[session_id]
                    
                    # Update statistics
                    if session_id in self.session_stats:
                        self.session_stats[session_id]['status'] = 'closed'
                        self.session_stats[session_id]['closed'] = time.time()
                    
                    logger.info(f"🔒 Session {session_id} closed (active: {len(self.active_sessions)}/{MAX_CONCURRENT_SESSIONS})")
                    
                    # Process queued sessions
                    self._process_queue()
                    return True
                else:
                    logger.warning(f"⚠️ Session {session_id} not found for closing")
                    return False
            finally:
                self._lock.release()
                    
        except Exception as e:
            logger.error(f"❌ Error closing session {session_id}: {e}")
            return False
    
    def _process_queue(self):
        """Process queued sessions when slots become available."""
        try:
            # Use a timeout to prevent deadlocks
            if not self._lock.acquire(timeout=5.0):
                logger.error(f"❌ Timeout acquiring lock for _process_queue")
                return
            
            try:
                while not self.session_queue.empty() and len(self.active_sessions) < MAX_CONCURRENT_SESSIONS:
                    session_id, output_dir = self.session_queue.get_nowait()
                    
                    # Create the queued session
                    session = AlignedPerUserSink(output_dir)
                    self.active_sessions[session_id] = session
                    
                    # Initialize session statistics
                    self.session_stats[session_id] = {
                        'created': time.time(),
                        'last_activity': time.time(),
                        'status': 'active',
                        'output_dir': output_dir,
                        'was_queued': True
                    }
                    
                    logger.info(f"✅ Queued session {session_id} activated (active: {len(self.active_sessions)}/{MAX_CONCURRENT_SESSIONS})")
            finally:
                self._lock.release()
                    
        except Exception as e:
            logger.error(f"❌ Error processing session queue: {e}")
    
    def get_session_stats(self) -> dict:
        """Get comprehensive statistics about all sessions."""
        try:
            # Use a timeout to prevent deadlocks
            if not self._lock.acquire(timeout=5.0):
                logger.error(f"❌ Timeout acquiring lock for get_session_stats")
                return {
                    'active_sessions': 0,
                    'max_concurrent': MAX_CONCURRENT_SESSIONS,
                    'queued_sessions': 0,
                    'max_queue_size': SESSION_QUEUE_SIZE,
                    'sessions': {},
                    'error': 'Lock timeout'
                }
            
            try:
                stats = {
                    'active_sessions': len(self.active_sessions),
                    'max_concurrent': MAX_CONCURRENT_SESSIONS,
                    'queued_sessions': self.session_queue.qsize(),
                    'max_queue_size': SESSION_QUEUE_SIZE,
                    'sessions': {}
                }
                
                for session_id, session_data in self.session_stats.items():
                    stats['sessions'][session_id] = session_data.copy()
                
                return stats
            finally:
                self._lock.release()
                
        except Exception as e:
            logger.error(f"❌ Error getting session stats: {e}")
            return {
                'active_sessions': 0,
                'max_concurrent': MAX_CONCURRENT_SESSIONS,
                'queued_sessions': 0,
                'max_queue_size': SESSION_QUEUE_SIZE,
                'sessions': {},
                'error': str(e)
            }
    
    def shutdown(self):
        """Shutdown the batch processing manager."""
        try:
            self._cleanup_running = False
            
            # Get list of sessions to close without holding the lock
            sessions_to_close = []
            with self._lock:
                sessions_to_close = list(self.active_sessions.keys())
            
            # Close all active sessions (without holding the lock)
            for session_id in sessions_to_close:
                self._close_session_internal(session_id)
            
            # Wait for cleanup thread
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                self._cleanup_thread.join(timeout=5)
            
            logger.info("🛑 Batch processing manager shutdown complete")
            
        except Exception as e:
            logger.error(f"❌ Error during batch processing shutdown: {e}")
    
    def _close_session_internal(self, session_id: str) -> bool:
        """Internal method to close a session without lock acquisition (for shutdown)."""
        try:
            if session_id in self.active_sessions:
                session = self.active_sessions[session_id]
                session.cleanup()
                
                # Remove from active sessions
                del self.active_sessions[session_id]
                
                # Update statistics
                if session_id in self.session_stats:
                    self.session_stats[session_id]['status'] = 'closed'
                    self.session_stats[session_id]['closed'] = time.time()
                
                logger.info(f"🔒 Session {session_id} closed during shutdown (active: {len(self.active_sessions)}/{MAX_CONCURRENT_SESSIONS})")
                return True
            else:
                logger.warning(f"⚠️ Session {session_id} not found for closing during shutdown")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error closing session {session_id} during shutdown: {e}")
            return False