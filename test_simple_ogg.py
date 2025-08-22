#!/usr/bin/env python3
"""
Simple test to debug OGG writing issues.
"""

import os
import sys
import tempfile
import struct
import time

# Add the current directory to the path so we can import garmin_voice
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from garmin_voice import FFmpegRecorder, AudioPacket

def create_minimal_opus_packets():
    """Create minimal Opus packets for testing."""
    packets = []
    
    # Create just a few simple packets
    opus_packets = [
        b'\x7F\x45\x4C\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00',  # 16 bytes
        b'\x7F\x45\x4C\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01',  # 16 bytes
        b'\x7F\x45\x4C\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x02',  # 16 bytes
    ]
    
    user_id = 12345
    timestamp = time.time()
    
    for i, data in enumerate(opus_packets):
        packet = AudioPacket(data, timestamp + i * 0.02, user_id)  # 20ms intervals
        packets.append(packet)
    
    return packets

def test_minimal_ogg():
    """Test minimal OGG writing."""
    print("Testing minimal OGG writing...")
    
    # Create recorder
    recorder = FFmpegRecorder()
    
    # Create minimal packets
    packets = create_minimal_opus_packets()
    print(f"Created {len(packets)} minimal packets")
    
    # Create temporary file for testing
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
        test_filename = temp_file.name
    
    try:
        # Test direct OGG writing
        print(f"Testing _write_opus_to_ogg with {len(packets)} packets...")
        success = recorder._write_opus_to_ogg(packets, test_filename)
        
        if success:
            print(f"✅ Minimal OGG writing successful: {test_filename}")
            
            # Check file size
            file_size = os.path.getsize(test_filename)
            print(f"📁 File size: {file_size} bytes")
            
            # Check if file is readable
            if file_size > 0:
                print("✅ File created and has content")
                
                # Try to read the file header
                with open(test_filename, 'rb') as f:
                    header = f.read(64)
                    print(f"📄 File header (hex): {header.hex()}")
                    
                    # Check for OGG signature
                    if header.startswith(b'OggS'):
                        print("✅ OGG signature found")
                    else:
                        print("❌ OGG signature not found")
                
                # Test with opusinfo
                print("\nTesting with opusinfo...")
                try:
                    import subprocess
                    result = subprocess.run(
                        ['./opus/opusinfo.exe', test_filename],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    print(f"opusinfo output:\n{result.stdout}")
                    if result.stderr:
                        print(f"opusinfo errors:\n{result.stderr}")
                except Exception as e:
                    print(f"opusinfo test failed: {e}")
                        
            else:
                print("❌ File is empty")
        else:
            print("❌ Minimal OGG writing failed")
            
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

if __name__ == "__main__":
    print("=== Minimal OGG Writing Test ===")
    test_minimal_ogg()
    print("\n=== Test Complete ===") 