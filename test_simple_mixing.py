#!/usr/bin/env python3
"""
Simple test to verify mixing functionality works.
This is a minimal test to debug any import or basic functionality issues.
"""

import os
import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_imports():
    """Test if we can import the required modules."""
    try:
        logger.info("🔍 Testing imports...")
        
        # Test basic imports
        from garmin_voice import AlignedPerUserSink
        logger.info("✅ Successfully imported AlignedPerUserSink")
        
        from pydub import AudioSegment
        logger.info("✅ Successfully imported AudioSegment")
        
        from pydub.generators import Sine
        logger.info("✅ Successfully imported Sine generator")
        
        import config_loader as cfg
        logger.info("✅ Successfully imported config_loader")
        
        return True
        
    except ImportError as e:
        logger.error(f"❌ Import failed: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error during import: {e}")
        return False

def test_basic_functionality():
    """Test basic functionality without complex audio generation."""
    try:
        logger.info("🔍 Testing basic functionality...")
        
        from garmin_voice import AlignedPerUserSink
        
        # Create a test directory
        test_dir = Path("data/test_simple")
        test_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a simple test sink
        test_sink = AlignedPerUserSink(str(test_dir), garmin_manager=None)
        logger.info("✅ Successfully created AlignedPerUserSink")
        
        # Test if mixing_audio method exists
        if hasattr(test_sink, 'mixing_audio'):
            logger.info("✅ mixing_audio method exists")
        else:
            logger.error("❌ mixing_audio method not found")
            return False
        
        # Test if compress_recordings_post_process method exists
        if hasattr(test_sink, 'compress_recordings_post_process'):
            logger.info("✅ compress_recordings_post_process method exists")
        else:
            logger.error("❌ compress_recordings_post_process method not found")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Basic functionality test failed: {e}")
        return False

def test_environment_variables():
    """Test environment variable handling."""
    try:
        logger.info("🔍 Testing environment variables...")
        
        # Test current values
        compression_enabled = os.environ.get('GARMIN_SILENCE_COMPRESSION_ENABLED', 'true')
        mono_enabled = os.environ.get('GARMIN_CONVERT_TO_MONO', 'true')
        
        logger.info(f"   GARMIN_SILENCE_COMPRESSION_ENABLED: {compression_enabled}")
        logger.info(f"   GARMIN_CONVERT_TO_MONO: {mono_enabled}")
        
        # Test setting new values
        os.environ['GARMIN_SILENCE_COMPRESSION_ENABLED'] = 'false'
        os.environ['GARMIN_CONVERT_TO_MONO'] = 'false'
        
        logger.info("✅ Successfully set environment variables")
        
        # Test reading new values
        new_compression = os.environ.get('GARMIN_SILENCE_COMPRESSION_ENABLED')
        new_mono = os.environ.get('GARMIN_CONVERT_TO_MONO')
        
        logger.info(f"   New GARMIN_SILENCE_COMPRESSION_ENABLED: {new_compression}")
        logger.info(f"   New GARMIN_CONVERT_TO_MONO: {new_mono}")
        
        # Restore original values
        os.environ['GARMIN_SILENCE_COMPRESSION_ENABLED'] = compression_enabled
        os.environ['GARMIN_CONVERT_TO_MONO'] = mono_enabled
        
        logger.info("✅ Successfully restored original environment variables")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Environment variable test failed: {e}")
        return False

def main():
    """Run all basic tests."""
    logger.info("🚀 Starting Basic Functionality Tests")
    logger.info("=" * 50)
    
    tests = [
        ("Import Test", test_imports),
        ("Basic Functionality Test", test_basic_functionality),
        ("Environment Variables Test", test_environment_variables),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        logger.info(f"\n🔍 Running {test_name}...")
        try:
            success = test_func()
            results.append((test_name, success))
            if success:
                logger.info(f"✅ {test_name} PASSED")
            else:
                logger.error(f"❌ {test_name} FAILED")
        except Exception as e:
            logger.error(f"💥 {test_name} CRASHED: {e}")
            results.append((test_name, False))
    
    # Print summary
    logger.info("\n" + "=" * 50)
    logger.info("📊 TEST RESULTS SUMMARY")
    logger.info("=" * 50)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        logger.info(f"{test_name}: {status}")
    
    logger.info(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All tests passed! You can now run the performance tests.")
    else:
        logger.error("💥 Some tests failed. Please fix the issues before running performance tests.")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
