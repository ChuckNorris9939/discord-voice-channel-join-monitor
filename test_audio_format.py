#!/usr/bin/env python3
"""
Test script to analyze audio data format from Discord voice.
"""

import os
import sys
import time
import wave
import tempfile
from pathlib import Path

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from garmin_voice import FFmpegRecorder, AudioPacket

def test_audio_format():
    """Test to analyze audio data format."""
    print("Testing audio format detection...")
    
    # Create test data
    test_cases = [
        # Small packet (likely Opus) - non-zero data
        (b'\x01\x02\x03', "Small packet"),
        # Medium packet (likely Opus) - non-zero data
        (b'\x10' + b'\x01' * 50, "Medium packet"),
        # Large packet (likely PCM) - non-zero data
        (b'\x01' * 1920, "Large packet (PCM-like)"),
        # Very large packet (definitely PCM) - non-zero data
        (b'\x01' * 10000, "Very large packet (PCM-like)"),
    ]
    
    recorder = FFmpegRecorder()
    
    for data, description in test_cases:
        is_opus = recorder._is_opus_data(data)
        print(f"{description}: {len(data)} bytes -> {'Opus' if is_opus else 'PCM'}")
    
    print("\nTesting packet creation...")
    
    # Create some test packets
    packets = []
    for i, (data, description) in enumerate(test_cases):
        packet = AudioPacket(data, time.time(), 12345)
        packets.append(packet)
        print(f"Packet {i}: {description} -> Zero packet: {packet.is_zero_packet}")
    
    print(f"\nCreated {len(packets)} test packets")
    
    # Add packets to recorder
    for packet in packets:
        recorder.user_packets[12345].append(packet)
    
    print(f"Added {len(recorder.user_packets[12345])} packets to recorder")
    
    # Test saving (this will help us see what format is detected)
    print("\nTesting save functionality...")
    
    # Create a permanent file for inspection
    temp_path = "test_output.ogg"
    
    try:
        # Try to save the packets
        success = recorder.save_user_recording(12345, temp_path)
        print(f"Save result: {'Success' if success else 'Failed'}")
        
        if success and os.path.exists(temp_path):
            file_size = os.path.getsize(temp_path)
            print(f"File created: {temp_path} ({file_size} bytes)")
            
            # Check if it's a valid OGG file
            with open(temp_path, 'rb') as f:
                header = f.read(4)
                if header == b'OggS':
                    print("✅ Valid OGG file created!")
                else:
                    print(f"❌ Not a valid OGG file (header: {header})")
        else:
            print("❌ No file created")
            
    except Exception as e:
        print(f"❌ Error during save: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\nFile saved as: {temp_path}")
    print("You can now test it with: ./opus/opusinfo.exe test_output.ogg")

if __name__ == "__main__":
    test_audio_format() 