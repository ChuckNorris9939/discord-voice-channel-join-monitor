#!/usr/bin/env python3
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
