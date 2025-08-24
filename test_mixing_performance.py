#!/usr/bin/env python3
"""
Test script to measure mixing performance with different configurations.
Tests 4 scenarios with 20-minute audio, 2 users:
1. Compression ON, Mono ON
2. Compression OFF, Mono ON  
3. Compression ON, Mono OFF
4. Compression OFF, Mono OFF
"""

import os
import time
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List

# Import the functions we need to test
from garmin_voice import AlignedPerUserSink
from pydub import AudioSegment
import config_loader as cfg

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MixingPerformanceTester:
    """Test class for measuring mixing performance with different configurations."""
    
    def __init__(self):
        self.test_dir = Path("data/test_mixing_performance")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        
        # Test audio parameters
        self.duration_minutes = 20
        self.sample_rate = 48000
        self.channels = 2  # Stereo for testing
        self.sample_width = 2  # 16-bit
        
        # Test configurations
        self.test_configs = [
            {
                "name": "Test 1: Compression ON, Mono ON",
                "compression": True,
                "mono": True,
                "description": "20min audio, 2 users, compression on, mono convert on"
            },
            {
                "name": "Test 2: Compression OFF, Mono ON", 
                "compression": False,
                "mono": True,
                "description": "20min audio, 2 users, compression off, mono convert on"
            },
            {
                "name": "Test 3: Compression ON, Mono OFF",
                "compression": True,
                "mono": False,
                "description": "20min audio, 2 users, compression on, mono convert off"
            },
            {
                "name": "Test 4: Compression OFF, Mono OFF",
                "compression": False,
                "mono": False,
                "description": "20min audio, 2 users, compression off, mono convert off"
            }
        ]
        
        # Results storage
        self.results = []
        
    def generate_test_audio(self, duration_minutes: int, filename: str, user_id: int) -> str:
        """Generate test audio file with specified duration."""
        duration_ms = duration_minutes * 60 * 1000
        
        # Create a simple test tone that varies over time
        # This simulates real voice data better than silence
        sample_rate = self.sample_rate
        channels = self.channels
        
        # Generate a test tone that changes frequency every 30 seconds
        audio_segments = []
        segment_duration = 30 * 1000  # 30 seconds
        
        for i in range(0, duration_ms, segment_duration):
            segment_length = min(segment_duration, duration_ms - i)
            
            # Vary frequency to simulate different speech patterns
            frequency = 440 + (i // segment_duration) * 50  # 440Hz, 490Hz, 540Hz, etc.
            
            # Generate sine wave for this segment
            samples = int(segment_length * sample_rate / 1000)
            import numpy as np
            
            t = np.linspace(0, segment_length/1000, samples)
            # Create stereo audio with slight difference between channels
            left_channel = np.sin(2 * np.pi * frequency * t) * 0.3
            right_channel = np.sin(2 * np.pi * frequency * t + 0.1) * 0.3
            
            # Convert to 16-bit PCM
            left_pcm = (left_channel * 32767).astype(np.int16)
            right_pcm = (right_channel * 32767).astype(np.int16)
            
            # Interleave stereo
            stereo_pcm = np.empty((samples * 2,), dtype=np.int16)
            stereo_pcm[0::2] = left_pcm
            stereo_pcm[1::2] = right_pcm
            
            # Convert to AudioSegment
            segment = AudioSegment(
                stereo_pcm.tobytes(),
                sample_width=2,
                frame_rate=sample_rate,
                channels=channels
            )
            
            audio_segments.append(segment)
        
        # Combine all segments
        if len(audio_segments) == 1:
            final_audio = audio_segments[0]
        else:
            final_audio = audio_segments[0]
            for segment in audio_segments[1:]:
                final_audio += segment
        
        # Save to file
        file_path = self.test_dir / filename
        final_audio.export(str(file_path), format="mp3")
        
        logger.info(f"Generated test audio: {filename} ({duration_minutes}min, {len(final_audio)}ms)")
        return str(file_path)
    
    def create_test_session(self, test_name: str) -> Dict:
        """Create a test session with 2 users and 20-minute audio."""
        logger.info(f"Creating test session: {test_name}")
        
        # Generate test audio files
        user1_file = self.generate_test_audio(
            self.duration_minutes, 
            f"{test_name}_user1.mp3", 
            1
        )
        user2_file = self.generate_test_audio(
            self.duration_minutes, 
            f"{test_name}_user2.mp3", 
            2
        )
        
        # Create mock timeline data
        timeline_data = {
            'session_start': test_name,
            'session_duration_ms': self.duration_minutes * 60 * 1000,
            'users': {
                '1': {'username': 'user1', 'file_path': user1_file},
                '2': {'username': 'user2', 'file_path': user2_file}
            }
        }
        
        return {
            'wav_files': [user1_file, user2_file],
            'timeline_data': timeline_data,
            'test_name': test_name
        }
    
    def run_mixing_test(self, test_config: Dict, test_session: Dict) -> Dict:
        """Run a single mixing test with the specified configuration."""
        test_name = test_config['name']
        compression_enabled = test_config['compression']
        mono_enabled = test_config['mono']
        
        logger.info(f"🚀 Starting {test_name}")
        logger.info(f"   Configuration: compression={compression_enabled}, mono={mono_enabled}")
        
        # Set environment variables for this test
        original_compression = os.environ.get('GARMIN_SILENCE_COMPRESSION_ENABLED', 'true')
        original_mono = os.environ.get('GARMIN_CONVERT_TO_MONO', 'true')
        
        try:
            # Configure for this test
            os.environ['GARMIN_SILENCE_COMPRESSION_ENABLED'] = str(compression_enabled).lower()
            os.environ['GARMIN_CONVERT_TO_MONO'] = str(mono_enabled).lower()
            
            # Reload config to apply new settings
            cfg.load_all_settings()
            
            # Create test sink
            test_sink = AlignedPerUserSink(str(self.test_dir), garmin_manager=None)
            
            # Measure mixing time
            start_time = time.time()
            
            if compression_enabled:
                # Test with compression
                logger.info(f"   🔧 Running compression + mixing...")
                compression_result = test_sink.compress_recordings_post_process(
                    test_session['wav_files'], 
                    test_session['timeline_data']
                )
                if compression_result and 'mixed' in compression_result['compressed_files']:
                    mixing_success = True
                    mixed_file = compression_result['compressed_files']['mixed']
                else:
                    mixing_success = False
                    mixed_file = None
            else:
                # Test without compression (just mixing)
                logger.info(f"   🎵 Running mixing only...")
                mixing_result = test_sink.mixing_audio(
                    test_session['wav_files'], 
                    test_session['timeline_data'], 
                    is_compressed=False
                )
                if mixing_result and 'mixed' in mixing_result:
                    mixing_success = True
                    mixed_file = mixing_result['mixed']
                else:
                    mixing_success = False
                    mixed_file = None
            
            end_time = time.time()
            processing_time = end_time - start_time
            
            # Get file sizes
            original_size = sum(os.path.getsize(f) for f in test_session['wav_files'])
            mixed_size = os.path.getsize(mixed_file) if mixed_file and os.path.exists(mixed_file) else 0
            
            # Calculate compression ratio
            compression_ratio = (mixed_size / original_size * 100) if original_size > 0 else 0
            
            result = {
                'test_name': test_name,
                'compression_enabled': compression_enabled,
                'mono_enabled': mono_enabled,
                'processing_time': processing_time,
                'mixing_success': mixing_success,
                'original_size_mb': original_size / (1024 * 1024),
                'mixed_size_mb': mixed_size / (1024 * 1024),
                'compression_ratio': compression_ratio,
                'mixed_file': mixed_file
            }
            
            logger.info(f"   ✅ {test_name} completed in {processing_time:.2f}s")
            logger.info(f"   📊 Results: {mixed_size/(1024*1024):.1f}MB mixed file, {compression_ratio:.1f}% of original")
            
            return result
            
        except Exception as e:
            logger.error(f"   ❌ {test_name} failed: {e}")
            return {
                'test_name': test_name,
                'compression_enabled': compression_enabled,
                'mono_enabled': mono_enabled,
                'processing_time': 0,
                'mixing_success': False,
                'error': str(e)
            }
        finally:
            # Restore original environment variables
            os.environ['GARMIN_SILENCE_COMPRESSION_ENABLED'] = original_compression
            os.environ['GARMIN_CONVERT_TO_MONO'] = original_mono
            cfg.load_all_settings()
    
    def run_all_tests(self):
        """Run all 4 test configurations."""
        logger.info("🎯 Starting Mixing Performance Test Suite")
        logger.info(f"📁 Test directory: {self.test_dir}")
        logger.info(f"⏱️  Audio duration: {self.duration_minutes} minutes per user")
        logger.info(f"👥 Users: 2")
        logger.info("=" * 80)
        
        # Create test session (reuse for all tests)
        test_session = self.create_test_session("performance_test_20min")
        
        # Run all tests
        for test_config in self.test_configs:
            logger.info("")
            result = self.run_mixing_test(test_config, test_session)
            self.results.append(result)
            
            # Small delay between tests
            time.sleep(1)
        
        # Print summary
        self.print_summary()
        
        # Cleanup test files
        self.cleanup_test_files()
    
    def print_summary(self):
        """Print comprehensive test results summary."""
        logger.info("")
        logger.info("=" * 80)
        logger.info("📊 MIXING PERFORMANCE TEST RESULTS SUMMARY")
        logger.info("=" * 80)
        
        # Sort results by processing time
        sorted_results = sorted(self.results, key=lambda x: x['processing_time'])
        
        for i, result in enumerate(sorted_results, 1):
            if result['mixing_success']:
                logger.info(f"{i}. {result['test_name']}")
                logger.info(f"   ⏱️  Processing time: {result['processing_time']:.2f}s")
                logger.info(f"   📁 Mixed file size: {result['mixed_size_mb']:.1f}MB")
                logger.info(f"   📊 Compression ratio: {result['compression_ratio']:.1f}%")
                logger.info(f"   🎯 Configuration: compression={result['compression_enabled']}, mono={result['mono_enabled']}")
            else:
                logger.info(f"{i}. {result['test_name']} ❌ FAILED")
                logger.info(f"   💥 Error: {result.get('error', 'Unknown error')}")
            logger.info("")
        
        # Performance analysis
        successful_results = [r for r in self.results if r['mixing_success']]
        if len(successful_results) > 1:
            fastest = min(successful_results, key=lambda x: x['processing_time'])
            slowest = max(successful_results, key=lambda x: x['processing_time'])
            
            logger.info("🏆 PERFORMANCE ANALYSIS:")
            logger.info(f"   🥇 Fastest: {fastest['test_name']} ({fastest['processing_time']:.2f}s)")
            logger.info(f"   🐌 Slowest: {fastest['test_name']} ({slowest['processing_time']:.2f}s)")
            logger.info(f"   📈 Speed difference: {slowest['processing_time'] / fastest['processing_time']:.1f}x")
            
            # Configuration impact analysis
            compression_tests = [r for r in successful_results if r['compression_enabled']]
            no_compression_tests = [r for r in successful_results if not r['compression_enabled']]
            
            if compression_tests and no_compression_tests:
                avg_compression_time = sum(r['processing_time'] for r in compression_tests) / len(compression_tests)
                avg_no_compression_time = sum(r['processing_time'] for r in no_compression_tests) / len(no_compression_tests)
                
                logger.info(f"   🔧 Compression impact: {avg_compression_time / avg_no_compression_time:.1f}x slower")
    
    def cleanup_test_files(self):
        """Clean up test files to save disk space."""
        logger.info("🧹 Cleaning up test files...")
        try:
            if self.test_dir.exists():
                shutil.rmtree(self.test_dir)
                logger.info("✅ Test files cleaned up")
        except Exception as e:
            logger.warning(f"⚠️ Could not clean up test files: {e}")

def main():
    """Main function to run the performance tests."""
    tester = MixingPerformanceTester()
    
    try:
        tester.run_all_tests()
    except KeyboardInterrupt:
        logger.info("⏹️  Tests interrupted by user")
    except Exception as e:
        logger.error(f"💥 Test suite failed: {e}")
        raise

if __name__ == "__main__":
    main()
