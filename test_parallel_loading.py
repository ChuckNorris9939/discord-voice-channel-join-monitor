#!/usr/bin/env python3
"""
Test script to measure the performance improvement of parallel audio loading.
This tests OPTIMIZATION #1: Parallel Audio Loading
"""

import os
import time
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the current directory to Python path
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the optimized class
from garmin_voice import AlignedPerUserSink

def test_parallel_loading():
    """Test the parallel loading optimization."""
    
    # Create test instance
    test_sink = AlignedPerUserSink("data/test-output")
    
    # Test files (use the real recording files)
    test_files = [
        "data/aligned-recordings/24.08.25_15-28-13_chucknorris992.mp3",
        "data/aligned-recordings/24.08.25_15-28-13_chucknorris99.mp3"
    ]
    
    # Verify files exist
    for file_path in test_files:
        if not os.path.exists(file_path):
            logger.error(f"❌ Test file not found: {file_path}")
            return False
    
    logger.info("🚀 Testing OPTIMIZATION #1: Parallel Audio Loading")
    logger.info(f"📁 Test files: {[os.path.basename(f) for f in test_files]}")
    
    # Test 1: Sequential loading (old method simulation)
    logger.info("\n📊 Test 1: Sequential Loading (Old Method)")
    start_time = time.perf_counter()
    
    audio_segments_seq = {}
    max_length_seq = 0
    
    for wav_file in test_files:
        if os.path.exists(wav_file):
            from pydub import AudioSegment
            audio = AudioSegment.from_file(wav_file)
            audio_segments_seq[wav_file] = audio
            max_length_seq = max(max_length_seq, len(audio))
    
    seq_time = time.perf_counter() - start_time
    logger.info(f"⏱️  Sequential loading time: {seq_time:.3f}s")
    logger.info(f"📊 Loaded {len(audio_segments_seq)} files, max length: {max_length_seq}ms")
    
    # Test 2: Parallel loading (new method)
    logger.info("\n🚀 Test 2: Parallel Loading (New Method)")
    start_time = time.perf_counter()
    
    audio_segments_par, max_length_par = test_sink._load_audio_parallel(test_files)
    
    par_time = time.perf_counter() - start_time
    logger.info(f"⏱️  Parallel loading time: {par_time:.3f}s")
    logger.info(f"📊 Loaded {len(audio_segments_par)} files, max length: {max_length_par}ms")
    
    # Performance comparison
    if seq_time > 0 and par_time > 0:
        speedup = seq_time / par_time
        time_saved = seq_time - par_time
        improvement_pct = ((seq_time - par_time) / seq_time) * 100
        
        logger.info("\n🏆 PERFORMANCE RESULTS:")
        logger.info(f"📈 Speedup: {speedup:.2f}x faster")
        logger.info(f"⏰ Time saved: {time_saved:.3f}s")
        logger.info(f"💯 Improvement: {improvement_pct:.1f}%")
        
        if speedup > 1.5:
            logger.info("🎉 Excellent! Parallel loading is significantly faster!")
        elif speedup > 1.2:
            logger.info("✅ Good! Parallel loading shows noticeable improvement!")
        else:
            logger.info("⚠️  Minimal improvement - may need more files or different optimization")
    
    # Verify data integrity
    logger.info("\n🔍 Data Integrity Check:")
    if len(audio_segments_seq) == len(audio_segments_par):
        logger.info("✅ Same number of files loaded")
    else:
        logger.warning("⚠️  Different number of files loaded")
    
    if max_length_seq == max_length_par:
        logger.info("✅ Same maximum length detected")
    else:
        logger.warning("⚠️  Different maximum length detected")
    
    # Test mixing with parallel loading
    logger.info("\n🎵 Testing mixing with parallel loading:")
    try:
        timeline_data = {"test": True}
        result = test_sink.mixing_audio(test_files, timeline_data, is_compressed=False)
        
        if result and 'mixed' in result:
            logger.info("✅ Mixing with parallel loading successful!")
            logger.info(f"📁 Mixed file: {os.path.basename(result['mixed'])}")
            
            # Clean up test mixed file
            if os.path.exists(result['mixed']):
                os.remove(result['mixed'])
                logger.info("🧹 Cleaned up test mixed file")
        else:
            logger.warning("⚠️  Mixing with parallel loading failed")
    except Exception as e:
        logger.error(f"❌ Error testing mixing: {e}")
    
    return True

def main():
    """Main test function."""
    logger.info("🎯 Starting Parallel Loading Performance Test")
    logger.info("=" * 50)
    
    try:
        success = test_parallel_loading()
        if success:
            logger.info("\n✅ Test completed successfully!")
        else:
            logger.error("\n❌ Test failed!")
    except Exception as e:
        logger.error(f"💥 Test crashed: {e}", exc_info=True)
    
    logger.info("=" * 50)
    logger.info("🏁 Test finished")

if __name__ == "__main__":
    main()
