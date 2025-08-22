#!/usr/bin/env python3
"""
Quick test for the aligned recording implementation.
Tests basic functionality without Discord dependencies.
"""
import os
import sys
import time
import tempfile
import wave
from unittest.mock import Mock

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from garmin_voice import AlignedPerUserSink, validate_aligned_recordings

class MockUser:
    def __init__(self, user_id: int, name: str):
        self.id = user_id
        self.name = name

class MockVoiceData:
    def __init__(self, pcm_data: bytes, duration_ms: float = 20.0):
        self.pcm = pcm_data
        self.duration = duration_ms

def generate_test_tone(duration_ms: float, frequency: float = 440.0) -> bytes:
    """Generate a simple test tone in stereo 16-bit PCM format."""
    import math
    
    sample_rate = 48000
    samples = int(duration_ms * sample_rate / 1000)
    
    pcm_data = bytearray()
    for i in range(samples):
        t = i / sample_rate
        sample_value = int(0.1 * 32767 * math.sin(2 * math.pi * frequency * t))
        
        # Stereo: left and right channel
        pcm_data.extend(sample_value.to_bytes(2, 'little', signed=True))
        pcm_data.extend(sample_value.to_bytes(2, 'little', signed=True))
    
    return bytes(pcm_data)

def test_basic_functionality():
    """Test basic aligned recording functionality."""
    print("🧪 Testing aligned recording basic functionality")
    print("=" * 50)
    
    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "recordings")
        
        # Create sink
        sink = AlignedPerUserSink(output_dir)
        print(f"✅ Created AlignedPerUserSink in {output_dir}")
        
        # Create test users
        user1 = MockUser(123, "Alice")
        user2 = MockUser(456, "Bob")
        
        # Simulate 2-second session with 20ms frames
        session_duration_s = 2.0
        frame_duration_ms = 20.0
        total_frames = int(session_duration_s * 1000 / frame_duration_ms)
        
        print(f"📊 Simulating {session_duration_s}s session ({total_frames} frames)")
        
        # Send audio frames
        frame_count = 0
        for frame_idx in range(total_frames):
            # User 1 speaks first second, User 2 speaks second second
            if frame_idx < total_frames // 2:
                # User 1 only
                pcm_data = generate_test_tone(frame_duration_ms, 440.0)  # A4
                voice_data = MockVoiceData(pcm_data, frame_duration_ms)
                sink.write(user1, voice_data)
                frame_count += 1
            else:
                # User 2 only
                pcm_data = generate_test_tone(frame_duration_ms, 880.0)  # A5
                voice_data = MockVoiceData(pcm_data, frame_duration_ms)
                sink.write(user2, voice_data)
                frame_count += 1
        
        print(f"📤 Sent {frame_count} voice frames")
        
        # Cleanup
        sink.cleanup()
        print("✅ Cleanup completed")
        
        # Check output files
        files = os.listdir(output_dir)
        wav_files = [f for f in files if f.endswith('.wav')]
        timeline_files = [f for f in files if f.startswith('timeline_')]
        
        print(f"📁 Generated files:")
        print(f"  WAV files: {len(wav_files)} - {wav_files}")
        print(f"  Timeline files: {len(timeline_files)} - {timeline_files}")
        
        if len(wav_files) != 2:
            print(f"❌ Expected 2 WAV files, got {len(wav_files)}")
            return False
        
        if len(timeline_files) != 1:
            print(f"❌ Expected 1 timeline file, got {len(timeline_files)}")
            return False
        
        # Validate recordings
        timeline_path = os.path.join(output_dir, timeline_files[0])
        results = validate_aligned_recordings(timeline_path)
        
        print(f"\n🔍 Validation Results:")
        print(f"  Valid: {'✅' if results['valid'] else '❌'}")
        print(f"  Expected duration: {results.get('expected_duration_ms', 0):.1f}ms")
        
        # Check each user file
        for user_id, user_result in results.get('users', {}).items():
            username = user_result['username']
            duration_ms = user_result['actual_duration_ms']
            duration_diff = user_result['duration_diff_ms']
            
            print(f"  {username}: {duration_ms:.1f}ms (diff: {duration_diff:.1f}ms)")
            
            # Check if WAV file is readable
            try:
                with wave.open(user_result['file_path'], 'rb') as wav_file:
                    frames = wav_file.getnframes()
                    sample_rate = wav_file.getframerate()
                    channels = wav_file.getnchannels()
                    print(f"    Format: {sample_rate}Hz, {channels}ch, {frames} frames")
            except Exception as e:
                print(f"    ❌ Error reading WAV: {e}")
                return False
        
        if results['errors']:
            print("  Errors:")
            for error in results['errors']:
                print(f"    - {error}")
        
        print("\n" + "=" * 50)
        return results['valid']

if __name__ == "__main__":
    try:
        success = test_basic_functionality()
        print(f"\n🎯 Test Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n💥 Test crashed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
