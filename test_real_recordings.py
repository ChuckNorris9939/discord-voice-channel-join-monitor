#!/usr/bin/env python3
"""
Real recording performance test using actual user files.
Tests 4 scenarios with real 20-minute audio files:
1. Compression ON, Mono ON
2. Compression OFF, Mono ON  
3. Compression ON, Mono OFF
4. Compression OFF, Mono OFF

Uses actual recording files:
- data/aligned-recordings/24.08.25_15-28-13_chucknorris992.mp3
- data/aligned-recordings/24.08.25_15-28-13_chucknorris99.mp3
"""

import os
import time
import logging
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

class RealRecordingPerformanceTester:
    """Test class for measuring mixing performance with real recording files."""
    
    def __init__(self):
        self.test_dir = Path("data/test_real_recordings")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        
        # Real recording files to use
        self.recording_files = [
            "data/aligned-recordings/24.08.25_15-28-13_chucknorris992.mp3",
            "data/aligned-recordings/24.08.25_15-28-13_chucknorris99.mp3"
        ]
        
        # Test configurations
        self.test_configs = [
            {
                "name": "Test 1: Compression ON, Mono ON",
                "compression": True,
                "mono": True,
                "description": "Real recordings, compression on, mono convert on"
            },
            {
                "name": "Test 2: Compression OFF, Mono ON", 
                "compression": False,
                "mono": True,
                "description": "Real recordings, compression off, mono convert on"
            },
            {
                "name": "Test 3: Compression ON, Mono OFF",
                "compression": True,
                "mono": False,
                "description": "Real recordings, compression on, mono convert off"
            },
            {
                "name": "Test 4: Compression OFF, Mono OFF",
                "compression": False,
                "mono": False,
                "description": "Real recordings, compression off, mono convert off"
            }
        ]
        
        # Results storage
        self.results = []
        
    def verify_recording_files(self) -> bool:
        """Verify that the recording files exist and are accessible."""
        logger.info("🔍 Verifying recording files...")
        
        for file_path in self.recording_files:
            if not os.path.exists(file_path):
                logger.error(f"❌ Recording file not found: {file_path}")
                return False
            
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            logger.info(f"✅ Found: {file_path} ({file_size_mb:.1f}MB)")
        
        return True
    
    def get_audio_info(self) -> Dict:
        """Get information about the audio files."""
        logger.info("📊 Analyzing audio files...")
        
        audio_info = {}
        total_duration = 0
        
        for file_path in self.recording_files:
            try:
                audio = AudioSegment.from_file(file_path)
                duration_ms = len(audio)
                duration_minutes = duration_ms / (1000 * 60)
                channels = audio.channels
                sample_rate = audio.frame_rate
                sample_width = audio.sample_width
                
                audio_info[file_path] = {
                    'duration_ms': duration_ms,
                    'duration_minutes': duration_minutes,
                    'channels': channels,
                    'sample_rate': sample_rate,
                    'sample_width': sample_width,
                    'file_size_mb': os.path.getsize(file_path) / (1024 * 1024)
                }
                
                total_duration += duration_ms
                
                logger.info(f"   📁 {os.path.basename(file_path)}:")
                logger.info(f"      ⏱️  Duration: {duration_minutes:.1f} minutes ({duration_ms}ms)")
                logger.info(f"      🔊 Channels: {channels}")
                logger.info(f"      🎵 Sample Rate: {sample_rate}Hz")
                logger.info(f"      💾 Sample Width: {sample_width * 8}-bit")
                logger.info(f"      📦 File Size: {audio_info[file_path]['file_size_mb']:.1f}MB")
                
            except Exception as e:
                logger.error(f"❌ Error analyzing {file_path}: {e}")
                return {}
        
        total_minutes = total_duration / (1000 * 60)
        logger.info(f"📊 Total session: {total_minutes:.1f} minutes ({total_duration}ms)")
        
        return audio_info
    
    def create_test_session(self) -> Dict:
        """Create a test session using the real recording files."""
        logger.info("Creating test session with real recordings...")
        
        # Create mock timeline data based on real files
        timeline_data = {
            'session_start': '24.08.25_15-28-13',
            'session_duration_ms': 0,  # Will be calculated from actual files
            'users': {}
        }
        
        # Get audio info to populate timeline
        audio_info = self.get_audio_info()
        if not audio_info:
            raise ValueError("Could not analyze audio files")
        
        # Calculate total duration
        total_duration = sum(info['duration_ms'] for info in audio_info.values())
        timeline_data['session_duration_ms'] = total_duration
        
        # Create user entries
        for i, file_path in enumerate(self.recording_files):
            username = os.path.basename(file_path).split('_')[-1].replace('.mp3', '')
            timeline_data['users'][str(i)] = {
                'username': username,
                'file_path': file_path
            }
        
        return {
            'wav_files': self.recording_files,
            'timeline_data': timeline_data,
            'audio_info': audio_info
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
        logger.info("🎯 Starting Real Recording Performance Test Suite")
        logger.info(f"📁 Test directory: {self.test_dir}")
        logger.info(f"📂 Using real recording files:")
        for file_path in self.recording_files:
            logger.info(f"   📄 {file_path}")
        logger.info("=" * 80)
        
        # Verify files exist
        if not self.verify_recording_files():
            logger.error("❌ Cannot proceed - recording files not found")
            return
        
        # Create test session
        test_session = self.create_test_session()
        
        # Run all tests
        for test_config in self.test_configs:
            logger.info("")
            result = self.run_mixing_test(test_config, test_session)
            self.results.append(result)
            
            # Small delay between tests
            time.sleep(1)
        
        # Print summary
        self.print_summary()
        
        # Cleanup test files (but keep the mixed files for inspection)
        self.cleanup_test_files()
    
    def print_summary(self):
        """Print comprehensive test results summary."""
        logger.info("")
        logger.info("=" * 80)
        logger.info("📊 REAL RECORDING PERFORMANCE TEST RESULTS SUMMARY")
        logger.info("=" * 80)
        
        # Sort results by processing time
        sorted_results = sorted(self.results, key=lambda x: x['processing_time'])
        
        for i, result in enumerate(sorted_results, 1):
            if result['mixing_success']:
                logger.info(f"{i}. {result['test_name']}")
                logger.info(f"   ⏱️  Processing time: {result['processing_time']:.2f}s")
                logger.info(f"   📁 Mixed file size: {result['mixed_size_mb']:.1f}MB")
                logger.info(f"   📊 Compression ratio: {result['compression_ratio']:.1f}%")
                logger.info(f"   🎯 Configuration: compression={result['compression_enabled']}, mono={result['compression_enabled']}")
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
            logger.info(f"   🐌 Slowest: {slowest['test_name']} ({slowest['processing_time']:.2f}s)")
            logger.info(f"   📈 Speed difference: {slowest['processing_time'] / fastest['processing_time']:.1f}x")
            
            # Configuration impact analysis
            compression_tests = [r for r in successful_results if r['compression_enabled']]
            no_compression_tests = [r for r in successful_results if not r['compression_enabled']]
            
            if compression_tests and no_compression_tests:
                avg_compression_time = sum(r['processing_time'] for r in compression_tests) / len(compression_tests)
                avg_no_compression_time = sum(r['processing_time'] for r in no_compression_tests) / len(no_compression_tests)
                
                logger.info(f"   🔧 Compression impact: {avg_compression_time / avg_no_compression_time:.1f}x slower")
    
    def cleanup_test_files(self):
        """Clean up test files but keep mixed files for inspection."""
        logger.info("🧹 Cleaning up test files...")
        try:
            # Keep the test directory and mixed files for inspection
            logger.info("📁 Test files and mixed outputs kept in: data/test_real_recordings/")
            logger.info("   You can inspect the mixed files to verify quality")
        except Exception as e:
            logger.warning(f"⚠️ Could not clean up test files: {e}")

def main():
    """Main function to run the performance tests."""
    tester = RealRecordingPerformanceTester()
    
    try:
        tester.run_all_tests()
    except KeyboardInterrupt:
        logger.info("⏹️  Tests interrupted by user")
    except Exception as e:
        logger.error(f"💥 Test suite failed: {e}")
        raise

if __name__ == "__main__":
    main()
