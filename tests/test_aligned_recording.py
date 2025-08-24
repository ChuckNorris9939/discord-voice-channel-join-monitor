#!/usr/bin/env python3
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
        
        print(f"\nSimulated {frame_count} voice packets over {total_frames} frames")
        
        # Finalize recording
        print("\n🔄 Finalizing recordings...")
        sink.finalize_session()
        
        # Find timeline file
        timeline_files = [f for f in os.listdir(output_dir) if f.startswith("timeline_")]
        if not timeline_files:
            print("❌ No timeline file found!")
            return False
        
        timeline_path = os.path.join(output_dir, timeline_files[0])
        print(f"📊 Timeline: {timeline_path}")
        
        # Validate recordings
        print("\n🔍 Validating aligned recordings...")
        results = validate_aligned_recordings(timeline_path)
        
        print(f"\nValidation Results:")
        print(f"  Valid: {'✅ YES' if results['valid'] else '❌ NO'}")
        print(f"  Expected duration: {results.get('expected_duration_ms', 0):.1f}ms")
        print(f"  Expected samples: {results.get('expected_samples', 0)}")
        
        if results['errors']:
            print("  Errors:")
            for error in results['errors']:
                print(f"    - {error}")
        
        print("\n📁 Generated files:")
        for file in sorted(os.listdir(output_dir)):
            file_path = os.path.join(output_dir, file)
            size_kb = os.path.getsize(file_path) / 1024
            print(f"  - {file} ({size_kb:.1f} KB)")
        
        print("\n" + "=" * 60)
        return results['valid']

if __name__ == "__main__":
    success = simulate_voice_session()
    print(f"\n🎯 Test result: {'SUCCESS' if success else 'FAILED'}")
    sys.exit(0 if success else 1)
