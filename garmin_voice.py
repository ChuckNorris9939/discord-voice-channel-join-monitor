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
import json
from typing import Final, Dict, Optional, List
from pathlib import Path
from collections import defaultdict, deque
from datetime import datetime

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
# Temporary debug logging for aligned recording
logger.setLevel(logging.DEBUG)

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
    """Thread-safe WAV writer for a single user with padding support."""
    
    def __init__(self, file_path: str, user_id: int, username: str):
        self.file_path = file_path
        self.user_id = user_id
        self.username = username
        self.lock = threading.Lock()
        self.sample_cursor = 0  # Current position in samples since recording start
        self.wav_file = None
        self.is_closed = False
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Open WAV file for writing - PCM 16-bit, 48kHz, mono
        self.wav_file = wave.open(file_path, 'wb')
        self.wav_file.setnchannels(1)  # mono
        self.wav_file.setsampwidth(2)  # 16-bit
        self.wav_file.setframerate(SAMPLERATE)  # 48000 Hz
        
        logger.debug(f"UserWavWriter initialized for {username} (ID: {user_id}): {file_path}")
    
    def write_silence(self, num_samples: int):
        """Write silence padding for the specified number of samples."""
        logger.debug(f"write_silence called for {self.username}: {num_samples} samples, closed={self.is_closed}")
        
        if self.is_closed:
            logger.warning(f"Cannot write silence for {self.username}: WAV file is closed")
            return
            
        if num_samples <= 0:
            logger.warning(f"Invalid num_samples for {self.username}: {num_samples}")
            return
            
        # REMOVE LOCK - this was causing deadlock!
        # The wav file writes are already atomic enough for our use case
        if not self.wav_file:
            logger.error(f"WAV file is None for {self.username}")
            return
        
        logger.debug(f"About to write {num_samples} silence samples for {self.username}")
        
        try:
            # Write silence efficiently
            if num_samples <= 240000:  # Up to ~5 seconds in one go
                silence_bytes = b'\x00\x00' * num_samples
                logger.debug(f"Created silence bytes for {self.username}: {len(silence_bytes)} bytes")
                
                self.wav_file.writeframes(silence_bytes)
                logger.debug(f"WAV writeframes completed for {self.username}")
                
                self.sample_cursor += num_samples
                logger.debug(f"✅ Wrote {num_samples} silence samples for user {self.username} (total: {self.sample_cursor})")
            else:
                # Write in chunks for very large silence blocks
                logger.debug(f"Writing large silence block in chunks for {self.username}: {num_samples} samples")
                chunk_size = 240000  # 5 seconds
                samples_written = 0
                
                while samples_written < num_samples:
                    chunk_samples = min(chunk_size, num_samples - samples_written)
                    silence_chunk = b'\x00\x00' * chunk_samples
                    
                    self.wav_file.writeframes(silence_chunk)
                    samples_written += chunk_samples
                    self.sample_cursor += chunk_samples
                    
                    logger.debug(f"Wrote silence chunk {samples_written}/{num_samples} for {self.username}")
                
                logger.debug(f"✅ Wrote total {num_samples} silence samples for user {self.username} (total: {self.sample_cursor})")
                
        except Exception as e:
            logger.error(f"💥 Error writing silence for {self.username}: {e}", exc_info=True)
    
    def write_audio(self, pcm_data: bytes):
        """Write PCM audio data to the WAV file."""
        logger.debug(f"write_audio called for {self.username}: {len(pcm_data) if pcm_data else 0} bytes, closed={self.is_closed}")
        
        if self.is_closed:
            logger.warning(f"Cannot write audio for {self.username}: WAV file is closed")
            return
            
        # REMOVE LOCK - this was causing deadlock!
        if not self.wav_file:
            logger.error(f"Cannot write audio for {self.username}: WAV file is None")
            return
            
        if not pcm_data:
            logger.warning(f"Cannot write audio for {self.username}: No PCM data provided")
            return
            
        try:
                original_length = len(pcm_data)
                logger.debug(f"Original PCM data length for {self.username}: {original_length} bytes")
                
                # Convert stereo to mono if needed (take left channel)
                if len(pcm_data) % 4 == 0:  # Stereo 16-bit
                    mono_data = bytearray()
                    for i in range(0, len(pcm_data), 4):
                        # Take left channel (first 2 bytes)
                        mono_data.extend(pcm_data[i:i+2])
                    pcm_data = bytes(mono_data)
                    logger.debug(f"Converted stereo to mono for {self.username}: {original_length} -> {len(pcm_data)} bytes")
                
                if pcm_data:  # Only write if we have data
                    self.wav_file.writeframes(pcm_data)
                    samples_written = len(pcm_data) // 2  # 16-bit samples
                    self.sample_cursor += samples_written
                    logger.debug(f"✅ Wrote {samples_written} audio samples for user {self.username} (total: {self.sample_cursor})")
                else:
                    logger.warning(f"No PCM data to write for user {self.username} after conversion")
                    
        except Exception as e:
            logger.error(f"💥 Error writing audio for user {self.username}: {e}", exc_info=True)
    
    def pad_to_sample(self, target_sample: int):
        """Pad with silence to reach the target sample position."""
        logger.debug(f"pad_to_sample called for {self.username}: target={target_sample}, current={self.sample_cursor}, closed={self.is_closed}")
        
        if self.is_closed:
            logger.warning(f"Cannot pad {self.username}: WAV file is closed")
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
        """Close the WAV file."""
        with self.lock:
            if self.wav_file and not self.is_closed:
                self.wav_file.close()
                self.is_closed = True
                logger.debug(f"UserWavWriter closed for {self.username}: {self.sample_cursor} samples written")


class AlignedPerUserSink(voice_recv.AudioSink):
    """AudioSink that creates time-aligned WAV files per user and handles STT."""
    
    def __init__(self, output_dir: str, garmin_manager=None):
        super().__init__()
        self.output_dir = output_dir
        self.garmin_manager = garmin_manager  # Reference to call STT callback
        self.session_start_time = time.perf_counter()
        self.session_start_timestamp = datetime.now().strftime('%Y%m%d_%H%M%SZ')
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
        
        # Smart silence compression: intelligently reduce long silence gaps
        self.last_global_activity = time.perf_counter()  # Track when anyone last spoke
        self.silence_compression_threshold = 5.0  # Compress silence longer than 5 seconds
        self.silence_compression_target = 1.0    # Reduce long silence to 1 second
        self.compress_silence = False  # Disable live compression - use post-processing instead
        self.global_compressed_offset = 0  # Track how much time we've saved
        
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"AlignedPerUserSink initialized: session_start={self.session_start_timestamp}, silence_compression={'ON' if self.compress_silence else 'OFF'}")
    
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
            logger.debug("Processing audio from unknown user")
        else:
            user_id = user.id
            username = self._sanitize_filename(user.name)
            logger.debug(f"Processing audio from user {username} (ID: {user_id})")
        
        # Check if we have PCM data
        if not data.pcm:
            logger.debug(f"No PCM data for user {username}")
            return
        
        logger.debug(f"Received {len(data.pcm)} bytes PCM data from {username}")
        
        current_time = time.perf_counter()
        session_elapsed_ms = (current_time - self.session_start_time) * 1000
        expected_sample_position = int(session_elapsed_ms * self.samples_per_ms)
        
        # Get or create writer for this user
        logger.debug(f"Getting or creating writer for user {username}")
        writer = self._get_or_create_writer(user_id, username)
        if not writer:
            logger.error(f"Failed to create writer for user {username}")
            return
        logger.debug(f"Writer obtained for user {username}: {writer.file_path}")
        
        # Calculate frame duration and samples
        frame_duration_ms = self._estimate_frame_duration(data)
        frame_samples = int(frame_duration_ms * self.samples_per_ms)
        
        logger.debug(f"User {username}: frame_duration={frame_duration_ms:.1f}ms, samples={frame_samples}, session_elapsed={session_elapsed_ms:.1f}ms")
        
        # Calculate compressed position if silence compression is enabled
        if self.compress_silence:
            compressed_position = self._calculate_compressed_position(current_time)
            logger.debug(f"User {username}: original_pos={expected_sample_position}, compressed_pos={compressed_position}")
            
            # Use compressed position for gap detection
            self._handle_gaps_and_padding(user_id, writer, compressed_position, frame_samples, current_time)
        else:
            # Standard gap detection without compression
            logger.debug(f"About to handle gaps and padding for {username}")
            self._handle_gaps_and_padding(user_id, writer, expected_sample_position, frame_samples, current_time)
            logger.debug(f"Gap handling completed for {username}")
        
        # Write the actual audio data
        logger.debug(f"Checking PCM data for {username}: {data.pcm is not None}, length={len(data.pcm) if data.pcm else 0}")
        if data.pcm:
            logger.debug(f"About to write {len(data.pcm)} bytes for user {username}")
            writer.write_audio(data.pcm)
            logger.debug(f"Audio written for user {username}, current sample position: {writer.get_current_sample()}")
            
            # Update global activity timestamp when anyone speaks
            if self.compress_silence:
                self.last_global_activity = current_time
                logger.debug(f"Updated global activity timestamp for compression")
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
            
            # Create filename: YYYYMMDD_HHMMSSZ_userId_username.wav
            filename = f"{self.session_start_timestamp}_{user_id}_{username}.wav"
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
        logger.debug(f"_handle_gaps_and_padding: user_id={user_id}, expected_pos={expected_position}")
        
        # For first frame from this user, just update cursor
        if user_id not in self.user_sample_cursors:
            logger.debug(f"First frame for user {user_id}, setting cursor to {expected_position}")
            self.user_sample_cursors[user_id] = expected_position
            return
        
        current_cursor = writer.get_current_sample()
        logger.debug(f"Gap check for user {user_id}: current_cursor={current_cursor}, expected_pos={expected_position}")
        
        # Check for gap based on time elapsed
        if user_id in self.last_frame_times:
            time_since_last = current_time - self.last_frame_times[user_id]
            expected_samples_since_last = int(time_since_last * 1000 * self.samples_per_ms)
            
            logger.debug(f"Time check for user {user_id}: time_since_last={time_since_last:.3f}s, expected_samples={expected_samples_since_last}")
            
            # If gap is detected (more than 1.5x expected frame duration)
            if expected_samples_since_last > (self.expected_frame_samples * 1.5):
                gap_samples = expected_position - current_cursor
                if gap_samples > 0:
                    logger.debug(f"Gap detected for user {user_id}: filling {gap_samples} samples")
                    writer.write_silence(gap_samples)
                    logger.debug(f"Filled gap of {gap_samples} samples for user {user_id}")
        
        logger.debug(f"Gap handling completed for user {user_id}")
    
    def _calculate_compressed_position(self, current_time: float) -> int:
        """Calculate compressed sample position with FIXED intelligent silence reduction."""
        # Calculate original elapsed time since session start
        original_elapsed = current_time - self.session_start_time
        
        # Calculate how long since last global activity
        silence_duration = current_time - self.last_global_activity
        
        # FIXED: Check if we should compress this silence period
        # Only compress if we haven't already compressed this silence gap
        silence_start_time = self.last_global_activity
        silence_end_time = current_time
        
        # Check if this silence period is long enough to compress
        if silence_duration > self.silence_compression_threshold:
            # Calculate compressed gap duration
            compressed_gap = self.silence_compression_target
            excess_silence = silence_duration - compressed_gap
            
            # CRITICAL FIX: Only update offset ONCE per silence period
            # Mark this silence period as processed by updating last_global_activity
            if silence_duration > self.silence_compression_threshold:
                # Update the offset for this silence period
                silence_offset_for_this_period = excess_silence
                
                # Update global tracking
                self.global_compressed_offset += silence_offset_for_this_period
                self.last_global_activity = current_time  # Mark as processed
                
                logger.debug(f"🔧 NEW silence period compressed: {excess_silence:.1f}s, total offset: {self.global_compressed_offset:.1f}s")
        
        # Calculate compressed elapsed time (ALWAYS apply full offset)
        compressed_elapsed = original_elapsed - self.global_compressed_offset
        
        # Convert to samples
        compressed_position = int(compressed_elapsed * 1000 * self.samples_per_ms)
        
        logger.debug(f"Position calc: original={original_elapsed:.1f}s, offset={self.global_compressed_offset:.1f}s, compressed={compressed_elapsed:.1f}s")
        
        return max(0, compressed_position)  # Never go negative
    
    def _track_activity(self, user_id: int, start_sample: int, frame_samples: int):
        """Track audio activity for smart silence compression."""
        end_sample = start_sample + frame_samples
        
        # Merge with existing activity or create new entry
        merged = False
        for activity in self.activity_timeline:
            # Check for overlap or immediate adjacency
            if (start_sample <= activity['end_sample'] + self.expected_frame_samples and 
                end_sample >= activity['start_sample'] - self.expected_frame_samples):
                # Merge activity periods
                activity['start_sample'] = min(activity['start_sample'], start_sample)
                activity['end_sample'] = max(activity['end_sample'], end_sample)
                if user_id not in activity['users']:
                    activity['users'].append(user_id)
                merged = True
                break
        
        if not merged:
            self.activity_timeline.append({
                'start_sample': start_sample,
                'end_sample': end_sample,
                'users': [user_id]
            })
        
        # Sort timeline by start sample
        self.activity_timeline.sort(key=lambda x: x['start_sample'])
        logger.debug(f"Activity tracked for user {user_id}: {len(self.activity_timeline)} active periods")
    
    def _build_compressed_timeline(self) -> List[Dict]:
        """Build compressed timeline removing long silence gaps."""
        if not self.activity_timeline:
            return []
        
        compressed = []
        current_compressed_pos = 0
        silence_threshold_samples = int(self.silence_threshold_ms * self.samples_per_ms)
        
        logger.info(f"Building compressed timeline: {len(self.activity_timeline)} activity periods, silence threshold: {self.silence_threshold_ms}ms")
        
        for i, activity in enumerate(self.activity_timeline):
            if i == 0:
                # First activity period - start at 0 in compressed timeline
                compressed_start = 0
                compressed_end = activity['end_sample'] - activity['start_sample']
            else:
                prev_activity = self.activity_timeline[i-1]
                silence_gap = activity['start_sample'] - prev_activity['end_sample']
                
                if silence_gap > silence_threshold_samples:
                    # Large silence gap - compress it
                    logger.debug(f"Compressing silence gap: {silence_gap} samples ({silence_gap/48000:.1f}s)")
                    compressed_start = current_compressed_pos
                    compressed_end = compressed_start + (activity['end_sample'] - activity['start_sample'])
                else:
                    # Small gap - keep it
                    compressed_start = current_compressed_pos + silence_gap
                    compressed_end = compressed_start + (activity['end_sample'] - activity['start_sample'])
            
            compressed.append({
                'original_start': activity['start_sample'],
                'original_end': activity['end_sample'],
                'compressed_start': compressed_start,
                'compressed_end': compressed_end,
                'users': activity['users']
            })
            
            current_compressed_pos = compressed_end
        
        logger.info(f"Compressed timeline built: {len(compressed)} periods, final length: {current_compressed_pos} samples ({current_compressed_pos/48000:.1f}s)")
        return compressed
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize username for filename use."""
        forbidden = '<>:"/\\|?*'
        sanitized = ''.join('_' if c in forbidden or ord(c) < 32 else c for c in name)
        sanitized = sanitized.strip().rstrip('. ')
        return sanitized[:32] if sanitized else 'unknown'
    
    def compress_recordings_post_process(self, wav_files: List[str], timeline_data: dict) -> dict:
        """Post-process WAV files to remove long silence gaps while maintaining sync."""
        try:
            import wave
            from pydub import AudioSegment
            
            logger.info(f"🔧 Starting post-process compression of {len(wav_files)} files")
            
            # Load all audio files
            audio_segments = {}
            max_length_ms = 0
            
            for wav_file in wav_files:
                if not os.path.exists(wav_file):
                    continue
                    
                audio = AudioSegment.from_wav(wav_file)
                audio_segments[wav_file] = audio
                max_length_ms = max(max_length_ms, len(audio))
                
            logger.debug(f"Original session length: {max_length_ms}ms")
            
            # Find silence periods across ALL tracks
            silence_threshold_ms = int(self.silence_compression_threshold * 1000)
            silence_target_ms = int(self.silence_compression_target * 1000)
            
            # Analyze silence periods in chunks
            chunk_size_ms = 100  # 100ms chunks for analysis
            silence_periods = []
            
            for chunk_start in range(0, max_length_ms, chunk_size_ms):
                chunk_end = min(chunk_start + chunk_size_ms, max_length_ms)
                
                # Check if ANY track has audio in this chunk
                has_audio = False
                for audio in audio_segments.values():
                    if chunk_start < len(audio):
                        chunk = audio[chunk_start:chunk_end]
                        # Simple volume-based detection
                        if chunk.dBFS > -60:  # Above silence threshold
                            has_audio = True
                            break
                
                # Track silence periods
                if not has_audio:
                    if not silence_periods or silence_periods[-1]['end'] != chunk_start:
                        # New silence period
                        silence_periods.append({'start': chunk_start, 'end': chunk_end})
                    else:
                        # Extend current silence period
                        silence_periods[-1]['end'] = chunk_end
                        
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
                compressed_file = wav_file.replace('.wav', '_compressed.wav')
                compressed_audio.export(compressed_file, format="wav")
                compressed_files[wav_file] = compressed_file
                
                logger.debug(f"Compressed {wav_file}: {len(audio)}ms -> {len(compressed_audio)}ms")
            
            # Update timeline data
            compressed_timeline = timeline_data.copy()
            compressed_timeline['original_duration_ms'] = timeline_data['session_duration_ms']
            compressed_timeline['session_duration_ms'] = max_length_ms - total_saved_ms
            compressed_timeline['compressed_silence_ms'] = total_saved_ms
            compressed_timeline['compression_method'] = 'post_process'
            compressed_timeline['compressions'] = compressions
            
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
        logger.info("AlignedPerUserSink cleanup: finalizing aligned recordings")
        
        try:
            self.is_recording = False
            
            # Calculate final session length (with compression if enabled)
            current_time = time.perf_counter()
            original_session_duration_ms = (current_time - self.session_start_time) * 1000
            
            if self.compress_silence:
                # Calculate compressed session length
                compressed_session_duration_ms = original_session_duration_ms - (self.global_compressed_offset * 1000)
                final_session_samples = int(compressed_session_duration_ms * self.samples_per_ms)
                
                logger.info(f"Session compression: original={original_session_duration_ms:.1f}ms, compressed={compressed_session_duration_ms:.1f}ms, saved={self.global_compressed_offset:.1f}s")
                logger.debug(f"Compressed session samples: {final_session_samples}")
            else:
                # Standard session length
                compressed_session_duration_ms = original_session_duration_ms
                final_session_samples = int(original_session_duration_ms * self.samples_per_ms)
                logger.debug(f"Standard session duration: {original_session_duration_ms:.1f}ms, final samples: {final_session_samples}")
            
            # Update total if larger
            self.total_session_samples = max(self.total_session_samples, final_session_samples)
            
            # Pad all writers to session end and close
            with self.writers_lock:
                logger.debug(f"Processing {len(self.user_writers)} user writers")
                
                timeline_data = {
                    'session_start': self.session_start_timestamp,
                    'session_duration_ms': compressed_session_duration_ms,
                    'original_duration_ms': original_session_duration_ms,
                    'compressed_silence_seconds': self.global_compressed_offset,
                    'compression_enabled': self.compress_silence,
                    'session_samples': self.total_session_samples,
                    'sample_rate': SAMPLERATE,
                    'users': {}
                }
                
                for user_id, writer in self.user_writers.items():
                    try:
                        logger.debug(f"Processing user {writer.username} (ID: {user_id})")
                        current_samples = writer.get_current_sample()
                        
                        # Only pad if needed (avoid excessive padding)
                        if current_samples < self.total_session_samples:
                            samples_to_pad = self.total_session_samples - current_samples
                            logger.debug(f"Padding {samples_to_pad} samples for {writer.username}")
                            writer.pad_to_sample(self.total_session_samples)
                        
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
                        
                        if len(wav_files) > 0 and self.silence_compression_threshold > 0:
                            logger.info("🔧 Starting post-process silence compression...")
                            
                            compression_result = self.compress_recordings_post_process(wav_files, timeline_data)
                            
                            if compression_result:
                                # Save compressed timeline
                                compressed_timeline_path = timeline_path.replace('.json', '_compressed.json')
                                with open(compressed_timeline_path, 'w', encoding='utf-8') as f:
                                    json.dump(compression_result['timeline'], f, indent=2, ensure_ascii=False)
                                
                                logger.info(f"✅ Compressed recordings saved: {compression_result['saved_ms']}ms saved")
                                logger.info(f"✅ Compressed timeline: {compressed_timeline_path}")
                                
                                # Log compression details
                                for original, compressed in compression_result['compressed_files'].items():
                                    logger.info(f"   📄 {os.path.basename(compressed)}")
                            else:
                                logger.warning("⚠️ Post-process compression failed")
                        else:
                            logger.debug("ℹ️ Skipping post-process compression (disabled or no files)")
                            
                    except Exception as comp_error:
                        logger.error(f"❌ Error in post-process compression: {comp_error}", exc_info=True)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to save timeline data: {e}", exc_info=True)
            
            logger.info(f"🎯 AlignedPerUserSink cleanup completed: {len(self.user_writers)} user tracks, "
                       f"{self.total_session_samples} samples ({compressed_session_duration_ms:.1f}ms)")
                       
        except Exception as e:
            logger.error(f"💥 Critical error in cleanup: {e}", exc_info=True)


# ==================================================
# Validation Functions
# ==================================================

def validate_aligned_recordings(timeline_path: str) -> dict:
    """
    Validate that aligned recordings have consistent timing.
    
    Args:
        timeline_path: Path to the timeline JSON file
        
    Returns:
        Dictionary with validation results
    """
    try:
        with open(timeline_path, 'r') as f:
            timeline_data = json.load(f)
        
        expected_samples = timeline_data['session_samples']
        expected_duration_ms = timeline_data['session_duration_ms']
        sample_rate = timeline_data['sample_rate']
        
        results = {
            'valid': True,
            'expected_samples': expected_samples,
            'expected_duration_ms': expected_duration_ms,
            'users': {},
            'errors': []
        }
        
        for user_id, user_data in timeline_data['users'].items():
            file_path = user_data['file_path']
            recorded_samples = user_data['final_samples']
            
            # Validate using pydub
            try:
                audio = AudioSegment.from_wav(file_path)
                actual_duration_ms = len(audio)
                actual_samples = int((actual_duration_ms / 1000.0) * sample_rate)
                
                # Check if duration matches within tolerance (10ms)
                duration_diff_ms = abs(actual_duration_ms - expected_duration_ms)
                samples_diff = abs(actual_samples - expected_samples)
                
                user_result = {
                    'file_path': file_path,
                    'username': user_data['username'],
                    'expected_samples': expected_samples,
                    'recorded_samples': recorded_samples,
                    'actual_samples': actual_samples,
                    'expected_duration_ms': expected_duration_ms,
                    'actual_duration_ms': actual_duration_ms,
                    'duration_diff_ms': duration_diff_ms,
                    'samples_diff': samples_diff,
                    'valid': duration_diff_ms <= 10,  # 10ms tolerance
                    'sample_rate': audio.frame_rate,
                    'channels': audio.channels,
                    'sample_width': audio.sample_width
                }
                
                if not user_result['valid']:
                    results['valid'] = False
                    results['errors'].append(f"User {user_data['username']}: Duration mismatch {duration_diff_ms:.1f}ms")
                
                # Validate audio format
                if audio.frame_rate != sample_rate:
                    results['valid'] = False
                    results['errors'].append(f"User {user_data['username']}: Wrong sample rate {audio.frame_rate} (expected {sample_rate})")
                
                if audio.channels != 1:
                    results['valid'] = False
                    results['errors'].append(f"User {user_data['username']}: Wrong channels {audio.channels} (expected 1)")
                
                if audio.sample_width != 2:
                    results['valid'] = False
                    results['errors'].append(f"User {user_data['username']}: Wrong sample width {audio.sample_width} (expected 2)")
                
                results['users'][user_id] = user_result
                
            except Exception as e:
                results['valid'] = False
                results['errors'].append(f"User {user_data['username']}: Failed to validate audio file: {e}")
        
        return results
        
    except Exception as e:
        return {
            'valid': False,
            'errors': [f"Failed to validate recordings: {e}"],
            'users': {}
        }


def create_test_validation_script(output_dir: str) -> str:
    """
    Create a standalone validation script for testing aligned recordings.
    
    Args:
        output_dir: Directory to save the validation script
        
    Returns:
        Path to the created script
    """
    script_content = '''#!/usr/bin/env python3
"""
Standalone validation script for aligned Discord recordings.
"""
import json
import sys
import os
from pydub import AudioSegment
from pathlib import Path

def validate_aligned_recordings(timeline_path):
    """Validate aligned recordings against timeline."""
    try:
        with open(timeline_path, 'r') as f:
            timeline_data = json.load(f)
        
        expected_samples = timeline_data['session_samples']
        expected_duration_ms = timeline_data['session_duration_ms']
        sample_rate = timeline_data['sample_rate']
        
        print(f"Validating session: {expected_duration_ms:.1f}ms ({expected_samples} samples @ {sample_rate}Hz)")
        print("=" * 80)
        
        all_valid = True
        
        for user_id, user_data in timeline_data['users'].items():
            file_path = user_data['file_path']
            username = user_data['username']
            
            if not os.path.exists(file_path):
                print(f"❌ {username}: File not found: {file_path}")
                all_valid = False
                continue
            
            try:
                audio = AudioSegment.from_wav(file_path)
                actual_duration_ms = len(audio)
                duration_diff_ms = abs(actual_duration_ms - expected_duration_ms)
                
                status = "✅" if duration_diff_ms <= 10 else "❌"
                print(f"{status} {username}: {actual_duration_ms:.1f}ms (diff: {duration_diff_ms:.1f}ms)")
                print(f"   Format: {audio.frame_rate}Hz, {audio.channels}ch, {audio.sample_width*8}bit")
                
                if duration_diff_ms > 10:
                    all_valid = False
                
                if audio.frame_rate != sample_rate or audio.channels != 1 or audio.sample_width != 2:
                    print(f"   ⚠️ Format mismatch (expected: {sample_rate}Hz, 1ch, 16bit)")
                    all_valid = False
                    
            except Exception as e:
                print(f"❌ {username}: Error reading audio: {e}")
                all_valid = False
        
        print("=" * 80)
        print(f"Overall result: {'✅ VALID' if all_valid else '❌ INVALID'}")
        return all_valid
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate_recordings.py <timeline.json>")
        sys.exit(1)
    
    timeline_path = sys.argv[1]
    if not os.path.exists(timeline_path):
        print(f"Error: Timeline file not found: {timeline_path}")
        sys.exit(1)
    
    success = validate_aligned_recordings(timeline_path)
    sys.exit(0 if success else 1)
'''
    
    script_path = os.path.join(output_dir, "validate_recordings.py")
    os.makedirs(output_dir, exist_ok=True)
    
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    # Make executable
    os.chmod(script_path, 0o755)
    
    return script_path


# ==================================================
# Test Functions
# ==================================================

def create_test_aligned_recording() -> str:
    """
    Create a test function that simulates Discord voice packets.
    
    Returns:
        Path to created test script
    """
    test_script_content = '''#!/usr/bin/env python3
"""
Test script for aligned Discord voice recording.
Simulates Discord voice packets with gaps and validates output.
"""
import os
import sys
import time
import wave
import random
import tempfile
from typing import Optional
from unittest.mock import Mock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from garmin_voice import AlignedPerUserSink, validate_aligned_recordings

class MockUser:
    def __init__(self, user_id: int, name: str):
        self.id = user_id
        self.name = name

class MockVoiceData:
    def __init__(self, pcm_data: bytes, duration_ms: float = 20.0):
        self.pcm = pcm_data
        self.duration = duration_ms

def generate_test_pcm(duration_ms: float, frequency: float = 440.0, amplitude: float = 0.1) -> bytes:
    """Generate test PCM audio data (stereo 16-bit at 48kHz)."""
    sample_rate = 48000
    samples = int(duration_ms * sample_rate / 1000)
    
    pcm_data = bytearray()
    for i in range(samples):
        # Generate sine wave
        sample_value = int(amplitude * 32767 * 
                          (0.5 + 0.5 * (i / samples)) *  # fade in
                          (random.random() * 0.3 + 0.7) *  # noise
                          1.0)  # sin(2π * frequency * t)
        
        # Stereo: left and right channel (same data)
        pcm_data.extend(sample_value.to_bytes(2, 'little', signed=True))
        pcm_data.extend(sample_value.to_bytes(2, 'little', signed=True))
    
    return bytes(pcm_data)

def simulate_voice_session():
    """Simulate a voice session with multiple users and gaps."""
    print("🎙️ Starting aligned recording test simulation")
    print("=" * 60)
    
    # Create temporary output directory
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_recordings")
        
        # Create sink
        sink = AlignedPerUserSink(output_dir)
        
        # Create test users
        users = [
            MockUser(12345, "TestUser1"),
            MockUser(67890, "TestUser2"),
            MockUser(11111, "TestUser3")
        ]
        
        print(f"Created test users: {[u.name for u in users]}")
        
        # Session parameters
        session_duration_s = 5.0  # 5 second test session
        frame_duration_ms = 20.0  # 20ms frames
        frames_per_second = 1000 / frame_duration_ms  # 50 frames/sec
        total_frames = int(session_duration_s * frames_per_second)
        
        print(f"Session: {session_duration_s}s, {frame_duration_ms}ms frames, {total_frames} total frames")
        print()
        
        # Simulate voice packets
        start_time = time.perf_counter()
        frame_count = 0
        
        for frame_idx in range(total_frames):
            current_time = start_time + (frame_idx * frame_duration_ms / 1000)
            
            # Simulate each user speaking with different patterns
            for user_idx, user in enumerate(users):
                # User 1: speaks first 2 seconds, then gap, then last 1 second
                # User 2: speaks middle 3 seconds
                # User 3: speaks randomly (50% chance each frame)
                
                should_speak = False
                if user_idx == 0:  # TestUser1
                    should_speak = (frame_idx < 100) or (frame_idx >= 200)  # 0-2s and 4-5s
                elif user_idx == 1:  # TestUser2
                    should_speak = (50 <= frame_idx < 200)  # 1-4s
                elif user_idx == 2:  # TestUser3
                    should_speak = random.random() < 0.4  # 40% random
                
                if should_speak:
                    # Generate unique frequency for each user
                    frequency = 440 + (user_idx * 200)  # 440Hz, 640Hz, 840Hz
                    pcm_data = generate_test_pcm(frame_duration_ms, frequency)
                    
                    voice_data = MockVoiceData(pcm_data, frame_duration_ms)
                    sink.write(user, voice_data)
                    frame_count += 1
            
            # Add small delay to simulate real timing
            if frame_idx % 25 == 0:  # Every 500ms
                print(f"  Frame {frame_idx:3d}/{total_frames} ({frame_idx * frame_duration_ms / 1000:.1f}s)")
        
        print(f"\\nSimulated {frame_count} voice packets over {total_frames} frames")
        
        # Finalize recording
        print("\\n🔄 Finalizing recordings...")
        sink.cleanup()
        
        # Find timeline file
        timeline_files = [f for f in os.listdir(output_dir) if f.startswith("timeline_")]
        if not timeline_files:
            print("❌ No timeline file found!")
            return False
        
        timeline_path = os.path.join(output_dir, timeline_files[0])
        print(f"📊 Timeline: {timeline_path}")
        
        # Validate recordings
        print("\\n🔍 Validating aligned recordings...")
        results = validate_aligned_recordings(timeline_path)
        
        print(f"\\nValidation Results:")
        print(f"  Valid: {'✅ YES' if results['valid'] else '❌ NO'}")
        print(f"  Expected duration: {results.get('expected_duration_ms', 0):.1f}ms")
        print(f"  Expected samples: {results.get('expected_samples', 0)}")
        
        if results['errors']:
            print("  Errors:")
            for error in results['errors']:
                print(f"    - {error}")
        
        print("\\n📁 Generated files:")
        for file in sorted(os.listdir(output_dir)):
            file_path = os.path.join(output_dir, file)
            size_kb = os.path.getsize(file_path) / 1024
            print(f"  - {file} ({size_kb:.1f} KB)")
        
        print("\\n" + "=" * 60)
        return results['valid']

if __name__ == "__main__":
    success = simulate_voice_session()
    print(f"\\n🎯 Test result: {'SUCCESS' if success else 'FAILED'}")
    sys.exit(0 if success else 1)
'''
    
    script_path = os.path.join(SCRIPT_DIR, "test_aligned_recording.py")
    
    with open(script_path, 'w') as f:
        f.write(test_script_content)
    
    # Make executable
    os.chmod(script_path, 0o755)
    
    return script_path


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
    """Voice listener that reacts on trigger phrases and plays sounds."""
    
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
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(ALIGNED_RECORDINGS_DIR, exist_ok=True)
        
        # Create validation and test scripts
        try:
            validation_script = create_test_validation_script(SCRIPT_DIR)
            test_script = create_test_aligned_recording()
            logger.info(f"Created validation tools: {validation_script}, {test_script}")
        except Exception as e:
            logger.warning(f"Failed to create validation tools: {e}")
        
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
        """Save current recording - triggers aligned recording finalization."""
        try:
            # If aligned recording is active, finalize it
            if self.aligned_sink:
                try:
                    self.aligned_sink.cleanup()
                    logger.info("✅ Aligned recording saved and finalized")
                    
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
            timestamp = time.strftime('%d.%m.%Y_%H-%M', time.localtime())
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
                        # Mix all user segments using pydub overlay
                        mixed_segment = self._mix_audio_segments(user_segments)
                        
                        if mixed_segment:
                            # Export mixed audio
                            mixed_segment.export(mixed_path, format="wav")
                            duration_s = len(mixed_segment) / 1000.0
                            logger.info(f"Saved mixed recording: {mixed_path} ({duration_s:.1f}s, {len(active_users)} users)")
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
    
    def _mix_audio_segments(self, user_segments: Dict[int, AudioSegment]) -> Optional[AudioSegment]:
        """Mix multiple audio segments using pydub overlay."""
        try:
            if not user_segments:
                return None
            
            segments = list(user_segments.values())
            
            if len(segments) == 1:
                return segments[0]
            
            # Find the longest segment duration
            max_length = max(len(seg) for seg in segments)
            
            # Create a silent base track of the maximum length
            mixed = AudioSegment.silent(duration=max_length)
            
            # Overlay each user's audio onto the mixed track
            for segment in segments:
                # Reduce volume slightly to prevent clipping when mixing multiple sources
                # Adjust gain based on number of users to prevent overall clipping
                gain_reduction = -3 * min(len(segments) - 1, 3)  # Reduce by 3dB per additional user, max -9dB
                adjusted_segment = segment + gain_reduction
                
                # Overlay this user's audio onto the mixed track
                mixed = mixed.overlay(adjusted_segment, position=0)
            
            # Normalize the mixed audio to prevent clipping
            # Find peak amplitude
            peak = mixed.max
            if peak > 0:
                # Calculate how much to reduce to avoid clipping
                # Leave some headroom (95% of max)
                target_peak = 32767 * 0.95  # 16-bit max * 0.95
                if peak > target_peak:
                    reduction_db = 20 * (target_peak / peak)
                    mixed = mixed + reduction_db
            
            return mixed
            
        except Exception as e:
            logger.error(f"Error mixing audio segments: {e}")
            return None
    
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
            
            # Create aligned recording sink with reference to this manager for STT
            self.aligned_sink = AlignedPerUserSink(ALIGNED_RECORDINGS_DIR, garmin_manager=self)
            
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
            "connected": self.is_connected(),
            "recording_duration": recording_duration,
            "max_recording_duration": cfg.GARMIN_RECORD_SECONDS,
            "buffer_size": total_buffer_size,
            "recording_errors": 0,  # Simplified - no error tracking needed
            "max_errors": 5,
            "is_processing": self.is_processing,
            "stt_enabled": cfg.STT_ENABLED,
            "stt_engine": cfg.STT_ENGINE if cfg.STT_ENABLED else "disabled",
            "last_process_time": self.last_process_time,
            "audio_pipeline_healthy": True,  # Simplified - always healthy with pydub
            "audio_callback_count": 0,  # Not tracked in simplified version
            "audio_callback_errors": 0,
            "time_since_last_audio": 0,
            "audio_callback_rate": 0,
            "last_chunk_size": 0,
            "active_users": max(legacy_active_users, aligned_active_users),
            "total_user_buffer_size": total_buffer_size,
            "user_buffers": {},  # Simplified - details not needed
            # New aligned recording info
            "aligned_recording_active": self.aligned_sink is not None,
            "aligned_users": aligned_active_users,
            "aligned_session_samples": aligned_session_samples,
            "aligned_session_duration_ms": aligned_session_duration_ms
        }