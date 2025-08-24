#!/usr/bin/env python3
"""
Performance test for audio mixing with generated audio.
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
from pydub import AudioSegment
import numpy as np

class GeneratedAudioPerformanceTest:
    """Test mixing performance with generated audio files."""
    
    def __init__(self):
        # Test configurations - only 2 tests now
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
        
        # Audio generation parameters
        self.duration_minutes = 20
        self.sample_rate = 48000
        self.channels = 2  # Stereo input
        
    def generate_test_audio(self, filename: str, duration_minutes: int, has_speech: bool = True):
        """Generate test audio file with speech-like patterns."""
        try:
            duration_ms = duration_minutes * 60 * 1000
            
            if has_speech:
                # Generate speech-like audio with varying patterns
                audio = AudioSegment.silent(duration=duration_ms)
                
                # Add speech-like segments every 30 seconds
                for i in range(0, duration_ms, 30000):
                    segment_duration = min(5000, duration_ms - i)  # 5 second segments
                    if segment_duration > 0:
                        # Generate tone at speech frequencies (100-8000 Hz)
                        speech_tone = AudioSegment.sine(440, duration=segment_duration)  # A4 note
                        speech_tone = speech_tone + AudioSegment.sine(880, duration=segment_duration)  # A5 note
                        speech_tone = speech_tone + AudioSegment.sine(1760, duration=segment_duration)  # A6 note
                        
                        # Add some variation
                        speech_tone = speech_tone + AudioSegment.sine(220, duration=segment_duration)  # A3 note
                        
                        # Insert into audio
                        audio = audio.overlay(speech_tone, position=i)
            else:
                # Generate background noise
                audio = AudioSegment.silent(duration=duration_ms)
                
                # Add low-level noise
                for i in range(0, duration_ms, 10000):
                    noise_duration = min(2000, duration_ms - i)
                    if noise_duration > 0:
                        noise = AudioSegment.sine(60, duration=noise_duration)  # Low frequency
                        noise = noise - 30  # Reduce volume
                        audio = audio.overlay(noise, position=i)
            
            # Export as MP3
            audio.export(filename, format="mp3")
            logger.info(f"✅ Generated test audio: {os.path.basename(filename)} ({duration_minutes} minutes)")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error generating test audio {filename}: {e}")
            return False
    
    def create_test_files(self):
        """Create test audio files for performance testing."""
        logger.info("🎵 Creating test audio files...")
        
        # Create test directory
        test_dir = "data/test-audio"
        os.makedirs(test_dir, exist_ok=True)
        
        # Generate 2 user audio files
        user1_file = os.path.join(test_dir, f"user1_{self.duration_minutes}min.mp3")
        user2_file = os.path.join(test_dir, f"user2_{self.duration_minutes}min.mp3")
        
        # Generate files
        if not self.generate_test_audio(user1_file, self.duration_minutes, has_speech=True):
            return False
        if not self.generate_test_audio(user2_file, self.duration_minutes, has_speech=True):
            return False
        
        self.test_files = [user1_file, user2_file]
        logger.info(f"✅ Created {len(self.test_files)} test files in {test_dir}")
        return True
    
    def create_test_session(self, test_config):
        """Create a test session with the given configuration."""
        logger.info(f"\n🎯 Creating test session: {test_config['name']}")
        
        # Set environment variables
        for key, value in test_config['env_vars'].items():
            os.environ[key] = value
            logger.info(f"🔧 Set {key} = {value}")
        
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
        
        # Measure mixing performance
        start_time = time.perf_counter()
        
        try:
            if test_config['compression_enabled']:
                # Test with compression
                logger.info("🔧 Running with silence compression...")
                result = test_sink.compress_recordings_post_process(self.test_files, {"test": True})
                
                if result and 'compressed_files' in result:
                    mixed_file = result['compressed_files'].get('mixed')
                    if mixed_file:
                        logger.info(f"✅ Compressed mixed file created: {os.path.basename(mixed_file)}")
                    else:
                        logger.warning("⚠️ No compressed mixed file created")
                else:
                    logger.error("❌ Compression processing failed")
                    return None
            else:
                # Test without compression (direct mixing)
                logger.info("🎵 Running direct mixing without compression...")
                result = test_sink.mixing_audio(self.test_files, {"test": True}, is_compressed=False)
                
                if result and 'mixed' in result:
                    mixed_file = result['mixed']
                    logger.info(f"✅ Direct mixed file created: {os.path.basename(mixed_file)}")
                else:
                    logger.error("❌ Direct mixing failed")
                    return None
            
            end_time = time.perf_counter()
            processing_time = end_time - start_time
            
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
            
            return {
                'config': test_config,
                'processing_time': processing_time,
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
        logger.info("🎯 Starting Generated Audio Performance Tests")
        logger.info("=" * 60)
        
        # Create test files first
        if not self.create_test_files():
            logger.error("❌ Cannot proceed - failed to create test files")
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
        table_header = f"{'Test':<35} {'Compression':<12} {'Mono':<8} {'Time (s)':<10} {'File Size (MB)':<15} {'Status':<10}"
        table_separator = "-" * 80
        
        logger.info(table_header)
        logger.info(table_separator)
        
        for result in self.results:
            config = result['config']
            compression = "ON" if config['compression_enabled'] else "OFF"
            mono = "ON" if config['mono_enabled'] else "OFF"
            time_str = f"{result['processing_time']:.2f}"
            size_str = f"{result['file_size_mb']:.1f}"
            status = "✅ PASS" if result['mixed_file'] else "❌ FAIL"
            
            row = f"{config['name']:<35} {compression:<12} {mono:<8} {time_str:<10} {size_str:<15} {status:<10}"
            logger.info(row)
        
        logger.info(table_separator)
        
        # Performance analysis
        if len(self.results) >= 2:
            logger.info("\n📊 PERFORMANCE ANALYSIS:")
            
            # Find fastest and slowest
            fastest = min(self.results, key=lambda x: x['processing_time'])
            slowest = max(self.results, key=lambda x: x['processing_time'])
            
            speedup = slowest['processing_time'] / fastest['processing_time']
            time_saved = slowest['processing_time'] - fastest['processing_time']
            
            logger.info(f"🏃 Fastest: {fastest['config']['name']} ({fastest['processing_time']:.2f}s)")
            logger.info(f"🐌 Slowest: {slowest['config']['name']} ({slowest['processing_time']:.2f}s)")
            logger.info(f"📈 Speedup: {speedup:.2f}x faster")
            logger.info(f"⏰ Time saved: {time_saved:.2f}s")
            
            # Compression impact
            compression_test = next((r for r in self.results if r['config']['compression_enabled']), None)
            no_compression_test = next((r for r in self.results if not r['config']['compression_enabled']), None)
            
            if compression_test and no_compression_test:
                compression_impact = compression_test['processing_time'] / no_compression_test['processing_time']
                logger.info(f"🔧 Compression impact: {compression_impact:.2f}x slower when enabled")
        
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
        test = GeneratedAudioPerformanceTest()
        test.run_all_tests()
    except Exception as e:
        logger.error(f"💥 Test crashed: {e}", exc_info=True)

if __name__ == "__main__":
    main()
