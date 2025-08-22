#!/usr/bin/env python3
"""
Detailed debug test for OGG writing issues.
"""

import os
import sys
import tempfile
import struct
import time

# Add the current directory to the path so we can import garmin_voice
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from garmin_voice import FFmpegRecorder, AudioPacket

def test_ogg_writing_step_by_step():
    """Test OGG writing step by step to identify the issue."""
    print("Testing OGG writing step by step...")
    
    # Create recorder
    recorder = FFmpegRecorder()
    
    # Create minimal packets
    packets = [
        AudioPacket(b'\x7F\x45\x4C\x46\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00', time.time(), 12345),
    ]
    
    # Create temporary file for testing
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
        test_filename = temp_file.name
    
    try:
        # Test each step individually
        print(f"Testing with file: {test_filename}")
        
        # Test the new _convert_opus_to_ogg method
        print("\nTesting new _convert_opus_to_ogg method...")
        success = recorder._convert_opus_to_ogg(packets, test_filename)
        print(f"Conversion success: {success}")
        
        # Check final result
        final_size = os.path.getsize(test_filename)
        print(f"\nFinal file size: {final_size} bytes")
        
        if final_size > 0:
            # Read and analyze the file
            with open(test_filename, 'rb') as f:
                data = f.read()
                print(f"File data (hex, first 100 bytes): {data[:100].hex()}")
                
                # Check for OGG pages
                ogg_pages = []
                offset = 0
                while offset < len(data):
                    if data[offset:offset+4] == b'OggS':
                        # Found OGG page
                        if offset + 27 <= len(data):
                            page_header = data[offset:offset+27]
                            page_sequence = struct.unpack('<I', page_header[18:22])[0]
                            num_segments = page_header[26]
                            ogg_pages.append({
                                'offset': offset,
                                'sequence': page_sequence,
                                'segments': num_segments
                            })
                            print(f"Found OGG page at offset {offset}: sequence={page_sequence}, segments={num_segments}")
                        offset += 1
                    else:
                        offset += 1
                
                print(f"Found {len(ogg_pages)} OGG pages")
                
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
    print("=== OGG Writing Debug Test ===")
    test_ogg_writing_step_by_step()
    print("\n=== Test Complete ===") 