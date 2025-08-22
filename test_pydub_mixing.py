#!/usr/bin/env python3
"""
Simple test script to demonstrate pydub mixing of two WAV files.
This tests the simultaneous mixing approach (Option A) where all users start at 0s.
"""

import os
from pydub import AudioSegment
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_pydub_mixing():
    """Test pydub mixing of two WAV files."""
    
    # File paths
    file1 = r"data\garmin-output\21.08.2025_18-52_user_chucknorris99.wav"
    file2 = r"data\garmin-output\21.08.2025_18-52_user_chucknorris992.wav"
    output_file = r"data\garmin-output\test_mixed_output.wav"
    
    # Check if files exist
    if not os.path.exists(file1):
        logger.error(f"File 1 not found: {file1}")
        return False
    
    if not os.path.exists(file2):
        logger.error(f"File 2 not found: {file2}")
        return False
    
    try:
        logger.info("Loading audio files...")
        
        # Load the WAV files
        audio1 = AudioSegment.from_wav(file1)
        audio2 = AudioSegment.from_wav(file2)
        
        logger.info(f"File 1: {len(audio1)}ms ({audio1.duration_seconds:.2f}s)")
        logger.info(f"File 2: {len(audio2)}ms ({audio2.duration_seconds:.2f}s)")
        
        # Ensure both segments have the same length
        max_length = max(len(audio1), len(audio2))
        logger.info(f"Target length: {max_length}ms ({max_length/1000:.2f}s)")
        
        # Pad shorter segment with silence
        if len(audio1) < max_length:
            silence = AudioSegment.silent(duration=max_length - len(audio1))
            audio1 = audio1 + silence
            logger.info(f"Padded file 1 to {len(audio1)}ms")
        
        if len(audio2) < max_length:
            silence = AudioSegment.silent(duration=max_length - len(audio2))
            audio2 = audio2 + silence
            logger.info(f"Padded file 2 to {len(audio2)}ms")
        
        # Now both segments should be the same length
        logger.info(f"Final lengths - File 1: {len(audio1)}ms, File 2: {len(audio2)}ms")
        
        # SIMPLE OVERLAY: No position parameter = both start at 0s simultaneously
        logger.info("Mixing files using overlay (simultaneous playback)...")
        mixed_audio = audio1.overlay(audio2)
        
        logger.info(f"Mixed audio length: {len(mixed_audio)}ms ({mixed_audio.duration_seconds:.2f}s)")
        
        # Apply normalization to prevent clipping
        try:
            mixed_audio = mixed_audio.normalize()
            logger.info("Applied normalization")
        except Exception as e:
            logger.warning(f"Could not normalize: {e}")
        
        # Export the mixed audio
        logger.info(f"Exporting to: {output_file}")
        mixed_audio.export(
            output_file,
            format="wav",
            parameters=["-ar", "48000", "-ac", "2", "-sample_fmt", "s16"]
        )
        
        logger.info("✅ Mixing test completed successfully!")
        logger.info(f"Output file: {output_file}")
        logger.info(f"Output duration: {mixed_audio.duration_seconds:.2f} seconds")
        
        return True
        
    except Exception as e:
        logger.error(f"Error during mixing test: {e}")
        return False

def test_with_position_parameter():
    """Test pydub mixing with position parameter for comparison."""
    
    file1 = r"data\garmin-output\21.08.2025_18-52_user_chucknorris99.wav"
    file2 = r"data\garmin-output\21.08.2025_18-52_user_chucknorris992.wav"
    output_file = r"data\garmin-output\test_mixed_with_position.wav"
    
    try:
        logger.info("\n" + "="*50)
        logger.info("TESTING WITH POSITION PARAMETER")
        logger.info("="*50)
        
        # Load the WAV files
        audio1 = AudioSegment.from_wav(file1)
        audio2 = AudioSegment.from_wav(file2)
        
        # Create a base segment with the total duration
        total_duration = max(len(audio1), len(audio2))
        base_audio = AudioSegment.silent(duration=total_duration)
        
        logger.info(f"Base duration: {total_duration}ms ({total_duration/1000:.2f}s)")
        
        # Overlay first audio at position 0
        mixed_with_position = base_audio.overlay(audio1, position=0)
        logger.info("Overlaid first audio at position 0ms")
        
        # Overlay second audio at position 5000ms (5 seconds)
        mixed_with_position = mixed_with_position.overlay(audio2, position=5000)
        logger.info("Overlaid second audio at position 5000ms (5s)")
        
        # Export
        mixed_with_position.export(output_file, format="wav")
        logger.info(f"✅ Position-based mixing completed: {output_file}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error during position-based mixing test: {e}")
        return False

if __name__ == "__main__":
    logger.info("Starting pydub mixing tests...")
    
    # Test 1: Simple overlay (simultaneous)
    success1 = test_pydub_mixing()
    
    # Test 2: With position parameter (sequential)
    success2 = test_with_position_parameter()
    
    if success1 and success2:
        logger.info("\n🎉 All tests completed successfully!")
        logger.info("Check the output files in data\\garmin-output\\")
    else:
        logger.error("\n❌ Some tests failed. Check the logs above.")

