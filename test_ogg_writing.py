#!/usr/bin/env python3
"""
Test script to verify custom OGG writing functionality.
"""

import os
import sys
import tempfile
import struct
import time

# Add the current directory to the path so we can import garmin_voice
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from garmin_voice import FFmpegRecorder, AudioPacket

def create_realistic_opus_packets():
    """Create realistic Opus packets for testing."""
    packets = []
    
    # Create some realistic Opus packet data
    # These are simplified but should be valid Opus packets
    opus_packets = [
        # Small control packet
        b'\x00',
        # Typical Opus audio packet (20ms @ 48kHz)
        b'\x7F\x45\x4C\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00',
        # Another typical packet
        b'\x7F\x45\x4C\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01',
        # Larger packet
        b'\x7F' + b'\x00' * 100,  # 101 bytes
        # Medium packet
        b'\x7F' + b'\x01' * 50,   # 51 bytes
    ]
    
    user_id = 12345
    timestamp = time.time()
    
    for i, data in enumerate(opus_packets):
        packet = AudioPacket(data, timestamp + i * 0.02, user_id)  # 20ms intervals
        packets.append(packet)
    
    return packets

def test_custom_ogg_writing():
    """Test the custom OGG writing functionality."""
    print("Testing custom OGG writing...")
    
    # Create recorder
    recorder = FFmpegRecorder()
    
    # Create realistic Opus packets
    packets = create_realistic_opus_packets()
    print(f"Created {len(packets)} test packets")
    
    # Create temporary file for testing
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
        test_filename = temp_file.name
    
    try:
        # Test direct OGG writing
        print(f"Testing _write_opus_to_ogg with {len(packets)} packets...")
        success = recorder._write_opus_to_ogg(packets, test_filename)
        
        if success:
            print(f"✅ Custom OGG writing successful: {test_filename}")
            
            # Check file size
            file_size = os.path.getsize(test_filename)
            print(f"📁 File size: {file_size} bytes")
            
            # Check if file is readable
            if file_size > 0:
                print("✅ File created and has content")
                
                # Try to read the file header
                with open(test_filename, 'rb') as f:
                    header = f.read(32)
                    print(f"📄 File header (hex): {header.hex()}")
                    
                    # Check for OGG signature
                    if header.startswith(b'OggS'):
                        print("✅ OGG signature found")
                    else:
                        print("❌ OGG signature not found")
                        
            else:
                print("❌ File is empty")
        else:
            print("❌ Custom OGG writing failed")
            
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Clean up
        try:
            os.unlink(test_filename)
            print(f"🧹 Cleaned up test file: {test_filename}")
        except OSError:
            pass

def test_opus_detection():
    """Test the Opus data detection."""
    print("\nTesting Opus data detection...")
    
    recorder = FFmpegRecorder()
    
    # Test various data types
    test_data = [
        (b'\x00', "Single byte"),
        (b'\x7F\x45\x4C\x46', "Small packet"),
        (b'\x7F' + b'\x00' * 100, "Medium packet"),
        (b'\x7F' + b'\x00' * 1000, "Large packet"),
        (b'\x00' * 2000, "PCM-like data"),
    ]
    
    for data, description in test_data:
        is_opus = recorder._is_opus_data(data)
        print(f"{description} ({len(data)} bytes): {'Opus' if is_opus else 'PCM'}")

if __name__ == "__main__":
    print("=== Custom OGG Writing Test ===")
    
    test_opus_detection()
    test_custom_ogg_writing()
    
    print("\n=== Test Complete ===") 