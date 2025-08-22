import os
import time
import logging
from pathlib import Path
from typing import Tuple, List
import config_loader as cfg

logger = logging.getLogger("discord_bot.audio_cleanup")

class AudioCleanupService:
    """Service for automatically cleaning up old audio files based on configurable retention periods."""
    
    def __init__(self):
        self.aligned_recordings_path = Path("data/aligned-recordings")
        self.garmin_output_path = Path("data/garmin-output")
        
    def cleanup_audio_files(self) -> Tuple[int, int]:
        """
        Clean up old audio files from both directories.
        
        Returns:
            Tuple[int, int]: (aligned_files_deleted, garmin_files_deleted)
        """
        try:
            aligned_deleted = self._cleanup_directory(
                self.aligned_recordings_path, 
                cfg.CLEANUP_ALIGNED_RECORDINGS_HOURS,
                "aligned-recordings"
            )
            
            garmin_deleted = self._cleanup_directory(
                self.garmin_output_path, 
                cfg.CLEANUP_GARMIN_OUTPUT_HOURS,
                "garmin-output"
            )
            
            total_deleted = aligned_deleted + garmin_deleted
            if total_deleted > 0:
                logger.info(f"Audio cleanup completed: {aligned_deleted} aligned files, {garmin_deleted} garmin files deleted")
            else:
                logger.debug("Audio cleanup completed: no files to delete")
                
            return aligned_deleted, garmin_deleted
            
        except Exception as e:
            logger.error(f"Error during audio cleanup: {e}", exc_info=True)
            return 0, 0
    
    def _cleanup_directory(self, directory_path: Path, retention_hours: int, directory_name: str) -> int:
        """
        Clean up files in a specific directory that are older than the retention period.
        
        Args:
            directory_path: Path to the directory to clean
            retention_hours: Number of hours to retain files
            directory_name: Human-readable name for logging
            
        Returns:
            int: Number of files deleted
        """
        if not directory_path.exists():
            logger.debug(f"Directory {directory_path} does not exist, skipping cleanup")
            return 0
            
        if not directory_path.is_dir():
            logger.warning(f"Path {directory_path} exists but is not a directory, skipping cleanup")
            return 0
        
        current_time = time.time()
        retention_seconds = retention_hours * 3600
        files_deleted = 0
        files_checked = 0
        
        try:
            for item in directory_path.iterdir():
                if item.is_file():
                    files_checked += 1
                    try:
                        # Get file modification time
                        mtime = item.stat().st_mtime
                        age_seconds = current_time - mtime
                        
                        if age_seconds > retention_seconds:
                            try:
                                item.unlink()
                                files_deleted += 1
                                logger.debug(f"Deleted old file: {item.name} (age: {age_seconds/3600:.1f}h)")
                            except PermissionError:
                                logger.warning(f"Permission denied deleting file: {item.name}")
                            except OSError as e:
                                logger.warning(f"OS error deleting file {item.name}: {e}")
                        else:
                            logger.debug(f"File {item.name} is recent (age: {age_seconds/3600:.1f}h), keeping")
                            
                    except OSError as e:
                        logger.warning(f"Error accessing file {item.name}: {e}")
                        continue
                        
        except PermissionError:
            logger.error(f"Permission denied accessing directory: {directory_path}")
            return 0
        except Exception as e:
            logger.error(f"Unexpected error during cleanup of {directory_path}: {e}", exc_info=True)
            return 0
            
        logger.info(f"Cleanup of {directory_name}: checked {files_checked} files, deleted {files_deleted} old files")
        return files_deleted
    
    def get_cleanup_stats(self) -> dict:
        """
        Get statistics about the cleanup directories.
        
        Returns:
            dict: Statistics about file counts and sizes
        """
        stats = {
            'aligned_recordings': self._get_directory_stats(self.aligned_recordings_path),
            'garmin_output': self._get_directory_stats(self.garmin_output_path)
        }
        return stats
    
    def _get_directory_stats(self, directory_path: Path) -> dict:
        """
        Get statistics for a specific directory.
        
        Args:
            directory_path: Path to the directory
            
        Returns:
            dict: Directory statistics
        """
        if not directory_path.exists() or not directory_path.is_dir():
            return {'exists': False, 'file_count': 0, 'total_size_mb': 0}
        
        try:
            file_count = 0
            total_size = 0
            
            for item in directory_path.iterdir():
                if item.is_file():
                    file_count += 1
                    try:
                        total_size += item.stat().st_size
                    except OSError:
                        pass  # Skip files we can't access
            
            return {
                'exists': True,
                'file_count': file_count,
                'total_size_mb': round(total_size / (1024 * 1024), 2)
            }
            
        except Exception as e:
            logger.warning(f"Error getting stats for {directory_path}: {e}")
            return {'exists': False, 'file_count': 0, 'total_size_mb': 0}

# Global instance
cleanup_service = AudioCleanupService()

def run_cleanup():
    """Convenience function to run the cleanup service."""
    return cleanup_service.cleanup_audio_files()

def get_cleanup_stats():
    """Convenience function to get cleanup statistics."""
    return cleanup_service.get_cleanup_stats()
