#!/usr/bin/env python3
"""
Performance test for audio mixing using real recording files.
Tests the impact of silence compression on mixing performance.
"""

import os
import time
import logging
import shutil
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the current directory to Python path
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the class
from garmin_voice import AlignedPerUserSink

class RealRecordingsPerformanceTest:
    """Test mixing performance with real recording files."""
    
    def __init__(self):
        # Real recording files (don't change these)
        self.recording_files = [
            "data/test_real_recordings/test_user_1.mp3",
            "data/test_real_recordings/test_user_2.mp3"
        ]
        
        # Test configurations
        self.test_configs = [
            {
                "name": "Test 1: Compression OFF, Mono ON",
                "compression_enabled": False,
                "mono_enabled": True,
                "env_vars": {
                    "GARMIN_SILENCE_COMPRESSION_ENABLED": "false",
                    "GARMIN_CONVERT_TO_MONO": "true"
                }
            },
            {
                "name": "Test 2: Compression ON, Mono ON", 
                "compression_enabled": True,
                "mono_enabled": True,
                "env_vars": {
                    "GARMIN_SILENCE_COMPRESSION_ENABLED": "true",
                    "GARMIN_CONVERT_TO_MONO": "true"
                }
            }
        ]
        
        # Results storage
        self.results = []
        
        # Create test output directory
        self.test_output_dir = "data/test-output"
        os.makedirs(self.test_output_dir, exist_ok=True)
        
        # Test timestamp for unique file naming
        self.test_timestamp = datetime.now().strftime('%d.%m.%y_%H-%M-%S')
    
    def verify_recording_files(self):
        """Verify that the real recording files exist and get their info."""
        logger.info("🔍 Verifying real recording files...")
        
        for file_path in self.recording_files:
            if not os.path.exists(file_path):
                logger.error(f"❌ Recording file not found: {file_path}")
                return False
            
            # Get file info
            file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
            logger.info(f"✅ {os.path.basename(file_path)}: {file_size:.1f} MB")
        
        return True
    
    def get_audio_info(self, file_path):
        """Get basic audio file information."""
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(file_path)
            return {
                'duration_ms': len(audio),
                'duration_min': len(audio) / 60000,
                'channels': audio.channels,
                'sample_rate': audio.frame_rate
            }
        except Exception as e:
            logger.error(f"Error getting audio info for {file_path}: {e}")
            return None
    
    def create_test_session(self, test_config):
        """Create a test session with the given configuration."""
        logger.info(f"\n🎯 Creating test session: {test_config['name']}")
        
        # Set environment variables
        for key, value in test_config['env_vars'].items():
            os.environ[key] = value
            logger.info(f"🔧 Set {key} = {value}")
        
        # OPTIMIZATION #5: Set advanced silence detection method for testing
        if test_config['compression_enabled']:
            # Test advanced silence detection
            os.environ['GARMIN_SILENCE_DETECTION_METHOD'] = 'advanced'
            os.environ['GARMIN_SILENCE_VAD_THRESHOLD'] = '0.3'
            os.environ['GARMIN_SILENCE_SPECTRAL_THRESHOLD'] = '0.15'
            os.environ['GARMIN_SILENCE_MIN_DURATION_MS'] = '500'
            logger.info("🔧 Set GARMIN_SILENCE_DETECTION_METHOD = advanced (OPTIMIZATION #5)")
        else:
            # Use simple detection for non-compression tests
            os.environ['GARMIN_SILENCE_DETECTION_METHOD'] = 'simple'
            logger.info("🔧 Set GARMIN_SILENCE_DETECTION_METHOD = simple")
        
        # OPTIMIZATION #6: Set batch processing settings for testing
        os.environ['GARMIN_BATCH_PROCESSING_ENABLED'] = 'true'
        os.environ['GARMIN_MAX_CONCURRENT_SESSIONS'] = '2'
        os.environ['GARMIN_SESSION_QUEUE_SIZE'] = '5'
        os.environ['GARMIN_BATCH_CLEANUP_INTERVAL'] = '10'
        logger.info("🔧 Set GARMIN_BATCH_PROCESSING_ENABLED = true (OPTIMIZATION #6)")
        
        # Create test sink
        test_dir = os.path.join(self.test_output_dir, f"test_{self.test_timestamp}")
        test_sink = AlignedPerUserSink(test_dir)
        
        # Update sink settings to match config
        test_sink.silence_compression_enabled = test_config['compression_enabled']
        
        return test_sink, test_dir
    
    def run_mixing_test(self, test_config):
        """Run a single mixing test."""
        logger.info(f"\n🚀 Running: {test_config['name']}")
        logger.info("=" * 60)
        
        # Create test session
        test_sink, test_dir = self.create_test_session(test_config)
        
        # Get audio info for logging
        audio_info = self.get_audio_info(self.recording_files[0])
        if audio_info:
            logger.info(f"📊 Audio info: {audio_info['duration_min']:.1f} minutes, {audio_info['channels']} channels, {audio_info['sample_rate']} Hz")
        
        # Initialize timing variables
        compression_time = 0
        mixing_time = 0
        total_time = 0
        
        try:
            if test_config['compression_enabled']:
                # Test with compression - DEFAULT workflow: compress individual files, then mix
                logger.info("🔧 Running with compression: DEFAULT workflow (compress individual files, then mix)...")
                
                # Step 1: Compress individual files first
                logger.info("🔧 Step 1: Compressing individual files...")
                compression_start = time.perf_counter()
                
                # Create timeline data for compression
                compression_timeline = {
                    "test": True,
                    "session_duration_ms": 1611237  # Use actual duration from real files
                }
                
                # Compress individual files (this will also create mixed file from compressed tracks)
                compression_result = test_sink.compress_recordings_post_process(self.recording_files, compression_timeline)
                compression_time = time.perf_counter() - compression_start
                
                if compression_result and 'compressed_files' in compression_result:
                    # Get the compressed mixed file
                    compressed_mixed_file = None
                    for file_type, file_path in compression_result['compressed_files'].items():
                        if file_type == 'mixed':
                            compressed_mixed_file = file_path
                            break
                    
                    if compressed_mixed_file:
                        logger.info(f"✅ Compressed mixed file created: {os.path.basename(compressed_mixed_file)}")
                        logger.info(f"⏱️  Compression time: {compression_time:.2f}s")
                        
                        # Use the compressed result
                        result = compression_result
                        mixing_time = 0  # Mixing is included in compression workflow
                    else:
                        logger.warning("⚠️ No compressed mixed file created")
                        result = None
                        compression_time = 0
                else:
                    logger.warning("⚠️ Compression failed")
                    result = None
                    compression_time = 0
            else:
                # Test without compression (direct mixing)
                logger.info("🎵 Running direct mixing without compression...")
                
                # Measure mixing time
                mixing_start = time.perf_counter()
                timeline_data = {"test": True}
                result = test_sink.mixing_audio(self.recording_files, timeline_data, is_compressed=False)
                mixing_time = time.perf_counter() - mixing_start
                
                if result and 'mixed' in result:
                    mixed_file = result['mixed']
                    logger.info(f"✅ Direct mixed file created: {os.path.basename(mixed_file)}")
                    logger.info(f"⏱️  Mixing time: {mixing_time:.2f}s")
                    compression_time = 0  # No compression
                else:
                    logger.error("❌ Direct mixing failed")
                    return None
            
            total_time = compression_time + mixing_time
            
            # For default workflow, mixing is included in compression time
            if test_config['compression_enabled']:
                total_time = compression_time  # Mixing is included in compression workflow
            
            # Copy mixed file to test output with descriptive name
            if 'mixed' in result:
                mixed_file = result['mixed']
                if os.path.exists(mixed_file):
                    # Create descriptive filename
                    compression_status = "compressed" if test_config['compression_enabled'] else "uncompressed"
                    mono_status = "mono" if test_config['mono_enabled'] else "stereo"
                    new_filename = f"{self.test_timestamp}_{compression_status}_{mono_status}_mixed.mp3"
                    dest_path = os.path.join(self.test_output_dir, new_filename)
                    
                    shutil.copy2(mixed_file, dest_path)
                    logger.info(f"📁 Mixed file saved to: {new_filename}")
                    
                    # Get file size
                    file_size = os.path.getsize(dest_path) / (1024 * 1024)  # MB
                    logger.info(f"📊 File size: {file_size:.1f} MB")
            elif test_config['compression_enabled'] and 'compressed_files' in result and 'mixed' in result['compressed_files']:
                # Handle compressed files case (default workflow)
                mixed_file = result['compressed_files']['mixed']
                if os.path.exists(mixed_file):
                    # Create descriptive filename
                    compression_status = "compressed" if test_config['compression_enabled'] else "uncompressed"
                    mono_status = "mono" if test_config['mono_enabled'] else "stereo"
                    new_filename = f"{self.test_timestamp}_{compression_status}_{mono_status}_mixed.mp3"
                    dest_path = os.path.join(self.test_output_dir, new_filename)
                    
                    shutil.copy2(mixed_file, dest_path)
                    logger.info(f"📁 Mixed file saved to: {new_filename}")
                    
                    # Get file size
                    file_size = os.path.getsize(dest_path) / (1024 * 1024)  # MB
                    logger.info(f"📊 File size: {file_size:.1f} MB")
            else:
                logger.warning("⚠️ No mixed file found in result")
                dest_path = None
                file_size = 0
            
            return {
                'config': test_config,
                'compression_time': compression_time,
                'mixing_time': mixing_time,
                'total_time': total_time,
                'mixed_file': dest_path if 'dest_path' in locals() else None,
                'file_size_mb': file_size if 'file_size' in locals() else 0
            }
            
        except Exception as e:
            logger.error(f"❌ Error during test: {e}", exc_info=True)
            return None
        finally:
            # Cleanup test files
            test_sink.cleanup()
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)
    
    def run_all_tests(self):
        """Run all performance tests."""
        logger.info("🎯 Starting Real Recordings Performance Tests")
        logger.info("=" * 60)
        
        # Verify files first
        if not self.verify_recording_files():
            logger.error("❌ Cannot proceed - recording files not found")
            return
        
        # Run each test
        for test_config in self.test_configs:
            result = self.run_mixing_test(test_config)
            if result:
                self.results.append(result)
            else:
                logger.error(f"❌ Test failed: {test_config['name']}")
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary."""
        if not self.results:
            logger.error("❌ No test results to display")
            return
        
        logger.info("\n" + "=" * 80)
        logger.info("🏆 PERFORMANCE TEST RESULTS SUMMARY")
        logger.info("=" * 80)
        
        # Create summary table
        table_header = f"{'Test':<35} {'Compression':<12} {'Mono':<8} {'Total (s)':<10} {'Comp (s)':<10} {'Mix (s)':<10} {'File Size (MB)':<15} {'Status':<10}"
        table_separator = "-" * 100
        
        logger.info(table_header)
        logger.info(table_separator)
        
        for result in self.results:
            config = result['config']
            compression = "ON" if config['compression_enabled'] else "OFF"
            mono = "ON" if config['mono_enabled'] else "OFF"
            total_str = f"{result['total_time']:.2f}"
            comp_str = f"{result['compression_time']:.2f}"
            mix_str = f"{result['mixing_time']:.2f}"
            size_str = f"{result['file_size_mb']:.1f}"
            status = "✅ PASS" if result['mixed_file'] else "❌ FAIL"
            
            row = f"{config['name']:<35} {compression:<12} {mono:<8} {total_str:<10} {comp_str:<10} {mix_str:<10} {size_str:<15} {status:<10}"
            logger.info(row)
        
        logger.info(table_separator)
        
        # Performance analysis
        if len(self.results) >= 2:
            logger.info("\n📊 PERFORMANCE ANALYSIS:")
            
            # Find fastest and slowest
            fastest = min(self.results, key=lambda x: x['total_time'])
            slowest = max(self.results, key=lambda x: x['total_time'])
            
            speedup = slowest['total_time'] / fastest['total_time']
            time_saved = slowest['total_time'] - fastest['total_time']
            
            logger.info(f"🏃 Fastest: {fastest['config']['name']} ({fastest['total_time']:.2f}s)")
            logger.info(f"🐌 Slowest: {slowest['config']['name']} ({slowest['total_time']:.2f}s)")
            logger.info(f"📈 Speedup: {speedup:.2f}x faster")
            logger.info(f"⏰ Time saved: {time_saved:.2f}s")
            
            # Compression impact
            compression_test = next((r for r in self.results if r['config']['compression_enabled']), None)
            no_compression_test = next((r for r in self.results if not r['config']['compression_enabled']), None)
            
            if compression_test and no_compression_test:
                compression_impact = compression_test['total_time'] / no_compression_test['total_time']
                logger.info(f"🔧 Compression impact: {compression_impact:.2f}x slower when enabled")
                
                # Detailed timing breakdown
                logger.info("\n⏱️ DETAILED TIMING BREAKDOWN:")
                logger.info(f"   📊 Test 1 (No Compression):")
                logger.info(f"      🎵 Mixing only: {no_compression_test['mixing_time']:.2f}s")
                logger.info(f"      🔧 Compression: {no_compression_test['compression_time']:.2f}s (N/A)")
                logger.info(f"      ⏱️  Total: {no_compression_test['total_time']:.2f}s")
                
                logger.info(f"   📊 Test 2 (With Compression):")
                logger.info(f"      🎵 Mixing included: {compression_test['mixing_time']:.2f}s (in compression)")
                logger.info(f"      🔧 Compression + mixing: {compression_test['compression_time']:.2f}s")
                logger.info(f"      ⏱️  Total: {compression_test['total_time']:.2f}s")
                
                # Calculate actual mixing performance difference
                if compression_test['mixing_time'] > 0 or no_compression_test['mixing_time'] > 0:
                    mixing_ratio = compression_test['compression_time'] / no_compression_test['mixing_time']
                    logger.info(f"\n   🎯 MIXING PERFORMANCE ANALYSIS:")
                    logger.info(f"      🎵 Direct mixing: {no_compression_test['mixing_time']:.2f}s")
                    logger.info(f"      🔧 Compression + mixing: {compression_test['compression_time']:.2f}s")
                    logger.info(f"      📈 Mixing is {mixing_ratio:.2f}x slower when compression is enabled")
                else:
                    logger.info(f"\n   🎯 MIXING PERFORMANCE ANALYSIS:")
                    logger.info(f"      🎵 Direct mixing: {no_compression_test['mixing_time']:.2f}s")
                    logger.info(f"      🔧 Compression includes mixing: {compression_test['compression_time']:.2f}s")
                    logger.info(f"      📊 Note: Mixing time is included in compression time (default workflow)")
        
        # File locations
        logger.info("\n📁 GENERATED FILES:")
        for result in self.results:
            if result['mixed_file']:
                filename = os.path.basename(result['mixed_file'])
                logger.info(f"✅ {filename}")
        
        logger.info(f"\n📂 All test files saved to: {self.test_output_dir}")
        logger.info("=" * 80)

def main():
    """Main test function."""
    try:
        test = RealRecordingsPerformanceTest()
        test.run_all_tests()
    except Exception as e:
        logger.error(f"💥 Test crashed: {e}", exc_info=True)

if __name__ == "__main__":
    main()
