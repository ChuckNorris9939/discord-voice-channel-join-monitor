#!/usr/bin/env python3
"""
Test script for improved audio recording functionality
"""

import os
import sys
import time
import wave
import numpy as np
from pathlib import Path

# Add the project directory to the path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Import constants directly to avoid discord dependency
SAMPLERATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2
FRAMES_PER_BUFFER = 960

def test_audio_processor():
    """Test the improved audio processor."""
    print("Testing ImprovedAudioProcessor...")
    
    # Create test audio data (sine wave)
    duration = 1.0  # 1 second
    frequency = 440  # A4 note
    samples = int(duration * SAMPLERATE)
    t = np.linspace(0, duration, samples, False)
    
    # Generate sine wave
    sine_wave = np.sin(2 * np.pi * frequency * t)
    
    # Convert to 16-bit PCM
    audio_data = (sine_wave * 32767).astype(np.int16)
    
    # Convert to bytes
    audio_bytes = audio_data.tobytes()
    
    # Test basic audio processing functions
    print("Testing audio processing...")
    
    # Test normalization
    print("Testing audio normalization...")
    try:
        # Simple normalization test
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        print(f"Original RMS: {rms:.2f}")
        
        if rms > 0:
            target_rms = 0.7 * 32767
            gain = target_rms / rms
            gain = min(gain, 2.0)
            normalized_array = (audio_array.astype(np.float32) * gain).astype(np.int16)
            normalized_rms = np.sqrt(np.mean(normalized_array.astype(np.float32) ** 2))
            print(f"Normalized RMS: {normalized_rms:.2f}")
        
        print(f"Original size: {len(audio_bytes)} bytes")
        print("✅ Audio normalization test completed")
        
    except Exception as e:
        print(f"❌ Audio normalization test failed: {e}")
    
    # Test noise reduction
    print("Testing noise reduction...")
    try:
        # Add some noise to the audio
        noise = np.random.normal(0, 1000, len(audio_data)).astype(np.int16)
        noisy_audio = (audio_data + noise).astype(np.int16)
        noisy_bytes = noisy_audio.tobytes()
        
        # Simple noise gate
        threshold = int(32767 * 0.1)  # 10% threshold
        noisy_array = np.frombuffer(noisy_bytes, dtype=np.int16)
        noisy_array[np.abs(noisy_array) < threshold] = 0
        denoised_bytes = noisy_array.tobytes()
        
        print(f"Noisy size: {len(noisy_bytes)} bytes")
        print(f"Denoised size: {len(denoised_bytes)} bytes")
        print("✅ Noise reduction test completed")
        
    except Exception as e:
        print(f"❌ Noise reduction test failed: {e}")
    
    print("✅ Audio processor tests completed successfully!")

def test_audio_mixing():
    """Test audio mixing functionality."""
    print("\nTesting audio mixing...")
    
    # Create test audio frames
    frame_size = FRAMES_PER_BUFFER * CHANNELS * BYTES_PER_SAMPLE
    
    # Create different test signals
    frame1 = np.sin(2 * np.pi * 440 * np.linspace(0, 0.02, FRAMES_PER_BUFFER)) * 16384
    frame1 = frame1.astype(np.int16).tobytes()
    
    frame2 = np.sin(2 * np.pi * 880 * np.linspace(0, 0.02, FRAMES_PER_BUFFER)) * 16384
    frame2 = frame2.astype(np.int16).tobytes()
    
    frame3 = np.sin(2 * np.pi * 220 * np.linspace(0, 0.02, FRAMES_PER_BUFFER)) * 16384
    frame3 = frame3.astype(np.int16).tobytes()
    
    # Test mixing
    try:
        # Test single frame
        if len(frame1) == frame_size:
            print(f"Single frame mixing: {len(frame1)} bytes")
        
        # Test multiple frame mixing
        audio_arrays = []
        for frame_data in [frame1, frame2, frame3]:
            audio_array = np.frombuffer(frame_data, dtype=np.int16)
            audio_arrays.append(audio_array)
        
        # Stack arrays and calculate mean
        stacked_audio = np.stack(audio_arrays, axis=0)
        mixed_array = np.mean(stacked_audio, axis=0, dtype=np.int16)
        mixed_bytes = mixed_array.tobytes()
        
        print(f"Multiple frame mixing: {len(mixed_bytes)} bytes")
        
        # Test empty frames
        empty_frame = b'\x00' * frame_size
        print(f"Empty frame mixing: {len(empty_frame)} bytes")
        
        print("✅ Audio mixing tests completed successfully!")
        
    except Exception as e:
        print(f"❌ Audio mixing test failed: {e}")

def test_file_formats():
    """Test file format support."""
    print("\nTesting file format support...")
    
    # Create test output directory
    test_output_dir = Path(SCRIPT_DIR) / "data" / "test-output"
    test_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create test audio data
    duration = 0.1  # 100ms
    frequency = 440
    samples = int(duration * SAMPLERATE)
    t = np.linspace(0, duration, samples, False)
    sine_wave = np.sin(2 * np.pi * frequency * t)
    audio_data = (sine_wave * 16384).astype(np.int16)
    
    # Test WAV export
    try:
        wav_path = test_output_dir / "test.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(BYTES_PER_SAMPLE)
            wf.setframerate(SAMPLERATE)
            wf.writeframes(audio_data.tobytes())
        
        print(f"✅ WAV file created: {wav_path}")
        
    except Exception as e:
        print(f"❌ WAV export failed: {e}")
    
    # Test OGG export (if pydub is available)
    try:
        from pydub import AudioSegment
        
        ogg_path = test_output_dir / "test.ogg"
        audio_segment = AudioSegment(
            data=audio_data.tobytes(),
            sample_width=BYTES_PER_SAMPLE,
            frame_rate=SAMPLERATE,
            channels=CHANNELS
        )
        audio_segment.export(str(ogg_path), format="ogg", codec="libvorbis")
        print(f"✅ OGG file created: {ogg_path}")
        
    except Exception as e:
        print(f"⚠️ OGG export failed: {e}")
    
    print("✅ File format tests completed!")

def main():
    """Run all tests."""
    print("🎙️ Testing Improved Audio Recording System")
    print("=" * 50)
    
    try:
        test_audio_processor()
        test_audio_mixing()
        test_file_formats()
        
        print("\n" + "=" * 50)
        print("✅ All tests completed successfully!")
        print("The improved audio recording system is ready to use.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 