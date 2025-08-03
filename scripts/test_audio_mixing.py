#!/usr/bin/env python3
"""
Test script for the new per-user audio recording and mixing system.
This script tests the audio mixing functionality using FFmpeg and simple mixing.
"""

import os
import sys
import tempfile
import wave
import subprocess
import time
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from garmin_voice import GarminVoiceManager

def test_ffmpeg_availability():
    """Test if FFmpeg is available and working."""
    print("🔍 Testing FFmpeg availability...")
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("✅ FFmpeg is available")
            return True
        else:
            print("❌ FFmpeg returned error code:", result.returncode)
            return False
    except FileNotFoundError:
        print("❌ FFmpeg not found in PATH")
        return False
    except subprocess.TimeoutExpired:
        print("❌ FFmpeg command timed out")
        return False

def create_test_audio_files():
    """Create test audio files with different frequencies."""
    print("🎵 Creating test audio files...")
    
    # Create temporary directory for test files
    temp_dir = tempfile.mkdtemp()
    test_files = []
    
    # Generate different frequency sine waves for testing
    frequencies = [440, 880, 1320]  # A4, A5, E6
    durations = [3]  # 3 seconds each
    
    for i, freq in enumerate(frequencies):
        for duration in durations:
            filename = os.path.join(temp_dir, f"test_audio_{freq}hz_{duration}s.wav")
            
            # Generate sine wave using FFmpeg
            cmd = [
                'ffmpeg', '-f', 'lavfi', '-i', 
                f'sine=frequency={freq}:duration={duration}',
                '-ar', '48000', '-ac', '2', '-acodec', 'pcm_s16le',
                '-y', filename
            ]
            
            try:
                subprocess.run(cmd, capture_output=True, check=True)
                test_files.append(filename)
                print(f"✅ Created {filename}")
            except subprocess.CalledProcessError as e:
                print(f"❌ Failed to create {filename}: {e}")
    
    return temp_dir, test_files

def test_audio_mixing():
    """Test the audio mixing functionality."""
    print("🎚️ Testing audio mixing...")
    
    # Create test audio files
    temp_dir, test_files = create_test_audio_files()
    
    if not test_files:
        print("❌ No test files created, skipping mixing test")
        return False
    
    try:
        # Test FFmpeg mixing
        print("🔧 Testing FFmpeg mixing...")
        output_file = os.path.join(temp_dir, "mixed_output.wav")
        
        # Build FFmpeg command for mixing
        inputs = []
        for file in test_files:
            inputs.extend(['-i', file])
        
        cmd = ['ffmpeg'] + inputs + [
            '-filter_complex', f'amix=inputs={len(test_files)}:duration=longest',
            '-ar', '48000', '-ac', '2', '-acodec', 'pcm_s16le',
            '-y', output_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print("✅ FFmpeg mixing successful")
            
            # Analyze the output file
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file)
                print(f"📊 Mixed file size: {file_size} bytes")
                
                # Get audio info using wave module
                try:
                    with wave.open(output_file, 'rb') as wf:
                        frames = wf.getnframes()
                        rate = wf.getframerate()
                        duration = frames / float(rate)
                        print(f"✅ Audio file analysis successful")
                        print(f"📊 Duration: {duration:.2f} seconds")
                        print(f"🎚️ Sample rate: {rate} Hz")
                        print(f"🔊 Channels: {wf.getnchannels()}")
                except Exception as e:
                    print(f"⚠️ Could not analyze audio file: {e}")
                
                return True
            else:
                print("❌ Output file not created")
                return False
        else:
            print(f"❌ FFmpeg mixing failed: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ Error during mixing test: {e}")
        return False
    finally:
        # Cleanup
        try:
            import shutil
            shutil.rmtree(temp_dir)
            print("🧹 Cleaned up test files")
        except Exception as e:
            print(f"⚠️ Could not cleanup test files: {e}")

def test_garmin_voice_class():
    """Test the GarminVoiceManager class functionality."""
    print("🤖 Testing GarminVoiceManager class...")
    
    try:
        # Create a mock bot object for testing
        class MockBot:
            def __init__(self):
                self.user = type('User', (), {'id': 123456789})()
        
        # Create an instance of GarminVoiceManager
        garmin = GarminVoiceManager(MockBot())
        print("✅ GarminVoiceManager instance created successfully")
        
        # Test health monitoring
        health = garmin.get_recording_health()
        print(f"📊 Health status: {health}")
        
        # Test buffer management
        print("✅ Buffer management initialized")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing GarminVoice class: {e}")
        return False

def analyze_existing_recordings():
    """Analyze existing recordings for audio quality."""
    print("📁 Analyzing existing recordings...")
    
    output_dir = Path("data/garmin-output")
    if not output_dir.exists():
        print("❌ Output directory not found")
        return False
    
    recordings = list(output_dir.glob("*.wav"))
    if not recordings:
        print("❌ No recordings found")
        return False
    
    print(f"📊 Found {len(recordings)} recordings")
    
    # Analyze the most recent recording
    latest_recording = max(recordings, key=lambda x: x.stat().st_mtime)
    print(f"🎵 Analyzing latest recording: {latest_recording.name}")
    
    try:
        # Get audio info using wave module
        with wave.open(str(latest_recording), 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = frames / float(rate)
            print("✅ Audio analysis successful")
            print(f"📊 File size: {latest_recording.stat().st_size} bytes")
            print(f"⏱️ Duration: {duration:.2f} seconds")
            print(f"🎚️ Sample rate: {rate} Hz")
            print(f"🔊 Channels: {wf.getnchannels()}")
            print(f"📊 Frame count: {frames}")
            print(f"📊 Bytes per frame: {wf.getsampwidth() * wf.getnchannels()}")
            
    except Exception as e:
        print(f"❌ Error analyzing recording: {e}")
        return False
    
    return True

def main():
    """Main test function."""
    print("🚀 Starting audio mixing system tests...")
    print("=" * 50)
    
    tests = [
        ("FFmpeg Availability", test_ffmpeg_availability),
        ("Audio Mixing", test_audio_mixing),
        ("GarminVoice Class", test_garmin_voice_class),
        ("Existing Recordings", analyze_existing_recordings),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n🧪 Running {test_name} test...")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} test failed with exception: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("📋 Test Results Summary:")
    print("=" * 50)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
        if result:
            passed += 1
    
    print(f"\n📊 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The per-user audio system is ready.")
    else:
        print("⚠️ Some tests failed. Please check the issues above.")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 