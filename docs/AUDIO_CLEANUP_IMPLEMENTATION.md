# Audio Cleanup Implementation

## Overview

This document describes the implementation of an automatic audio file cleanup routine for the Discord voice channel join monitor bot. The system automatically deletes old audio files based on configurable retention periods to prevent disk space issues.

## Features Implemented

### 1. **Automatic Cleanup Service**
- **Service Class**: `AudioCleanupService` in `audio_cleanup_service.py`
- **Target Directories**:
  - `data/aligned-recordings`: Default retention: 48 hours
  - `data/garmin-output`: Default retention: 72 hours
- **Robust Error Handling**: Gracefully handles missing directories, permission issues, and I/O errors
- **File-Only Deletion**: Only deletes files, preserves directory structure

### 2. **Configuration Management**
- **Database Integration**: Uses existing SQLite database (`user_log.db`)
- **New Configuration Keys**:
  - `CLEANUP_ALIGNED_RECORDINGS_HOURS`: Retention period for aligned recordings
  - `CLEANUP_GARMIN_OUTPUT_HOURS`: Retention period for garmin output files
- **Default Values**: 48h and 72h respectively
- **Dynamic Loading**: Values loaded from database on startup and config changes

### 3. **Web GUI Integration**
- **Settings Page**: New "🗑️ Audio Cleanup Settings" section
- **Configurable Retention**: Input fields for both retention periods (1-8760 hours range)
- **Manual Cleanup Button**: "🧹 Run Cleanup Now" button for immediate execution
- **Real-time Feedback**: Shows results of manual cleanup operations

### 4. **API Endpoints**
- **`POST /cleanup/run`**: Manually trigger cleanup process
- **`GET /cleanup/stats`**: Get current cleanup statistics
- **Response Format**: JSON with success status and file counts

### 5. **Automated Execution**
- **Startup Cleanup**: Runs once when bot starts
- **Periodic Cleanup**: Runs every 6 hours via Discord.py tasks
- **Graceful Shutdown**: Properly stops cleanup tasks during bot shutdown

### 6. **Monitoring & Statistics**
- **Home Dashboard**: Displays file counts and total size for both directories
- **Real-time Updates**: Auto-refreshes every 30 seconds
- **Comprehensive Logging**: Detailed logging of all cleanup operations

## Technical Implementation

### File Structure
```
├── audio_cleanup_service.py          # Core cleanup service
├── config_loader.py                  # Configuration management (updated)
├── main.py                          # Main application (updated)
├── templates/
│   ├── settings.html                # Settings page (updated)
│   └── home.html                    # Home dashboard (updated)
└── data/
    ├── aligned-recordings/          # Target directory 1
    └── garmin-output/               # Target directory 2
```

### Key Components

#### 1. AudioCleanupService Class
```python
class AudioCleanupService:
    def cleanup_audio_files(self) -> Tuple[int, int]:
        # Main cleanup method
        
    def _cleanup_directory(self, directory_path, retention_hours, directory_name):
        # Directory-specific cleanup logic
        
    def get_cleanup_stats(self) -> dict:
        # Get statistics for monitoring
```

#### 2. Configuration Integration
```python
# New database keys
DB_KEY_CLEANUP_ALIGNED_RECORDINGS_HOURS = "CLEANUP_ALIGNED_RECORDINGS_HOURS"
DB_KEY_CLEANUP_GARMIN_OUTPUT_HOURS = "CLEANUP_GARMIN_OUTPUT_HOURS"

# Global variables with defaults
CLEANUP_ALIGNED_RECORDINGS_HOURS = 48
CLEANUP_GARMIN_OUTPUT_HOURS = 72
```

#### 3. Periodic Task
```python
@tasks.loop(hours=6)
async def periodic_cleanup_task():
    """Periodically clean up old audio files"""
    # Runs every 6 hours
```

## Usage

### 1. **Configuration**
- Access the bot settings page (`/settings`)
- Navigate to "🗑️ Audio Cleanup Settings" section
- Adjust retention periods as needed
- Click "💾 Save Settings" to apply changes

### 2. **Manual Cleanup**
- Use "🧹 Run Cleanup Now" button for immediate cleanup
- View results in popup dialog
- Check logs for detailed operation information

### 3. **Monitoring**
- View real-time statistics on home dashboard
- Monitor file counts and disk usage
- Check logs for cleanup operation details

## Configuration Options

### Retention Periods
- **Range**: 1 hour to 8760 hours (1 year)
- **Default Values**:
  - Aligned recordings: 48 hours (2 days)
  - Garmin output: 72 hours (3 days)
- **Units**: Hours (converted to seconds internally)

### Cleanup Frequency
- **Startup**: Once when bot starts
- **Periodic**: Every 6 hours
- **Manual**: On-demand via web interface

## Safety Features

### 1. **Error Handling**
- Graceful handling of missing directories
- Permission error handling
- I/O error recovery
- Comprehensive logging without crashes

### 2. **File Safety**
- Only deletes files (preserves directories)
- Checks file modification time before deletion
- Respects configured retention periods
- No recursive directory deletion

### 3. **Operation Logging**
- Detailed logging of all operations
- File counts and deletion results
- Error reporting and debugging information
- Audit trail for compliance

## Performance Considerations

### 1. **Efficient File Operations**
- Uses `pathlib.Path` for modern file operations
- Single directory scan per cleanup operation
- Minimal memory usage during cleanup
- Non-blocking operations

### 2. **Resource Management**
- Cleanup runs in background tasks
- No impact on bot responsiveness
- Configurable execution frequency
- Graceful shutdown handling

## Monitoring & Maintenance

### 1. **Dashboard Metrics**
- Real-time file counts
- Total disk usage
- Directory existence status
- Auto-refreshing display

### 2. **Log Analysis**
- Cleanup operation timestamps
- File deletion counts
- Error reporting
- Performance metrics

### 3. **Health Checks**
- Directory accessibility
- File operation permissions
- Database configuration status
- Service availability

## Troubleshooting

### Common Issues

#### 1. **Permission Denied Errors**
- Check file/directory permissions
- Verify bot user access rights
- Review SELinux/AppArmor settings

#### 2. **Configuration Not Loading**
- Verify database connectivity
- Check configuration key names
- Review default value fallbacks

#### 3. **Cleanup Not Running**
- Check task scheduling
- Verify bot startup sequence
- Review error logs for details

### Debug Commands
```python
# Test cleanup service
from audio_cleanup_service import run_cleanup, get_cleanup_stats
result = run_cleanup()
stats = get_cleanup_stats()

# Check configuration
import config_loader as cfg
print(cfg.CLEANUP_ALIGNED_RECORDINGS_HOURS)
print(cfg.CLEANUP_GARMIN_OUTPUT_HOURS)
```

## Future Enhancements

### Potential Improvements
1. **File Type Filtering**: Only delete specific audio file types
2. **Size-Based Cleanup**: Cleanup based on total directory size
3. **Compression**: Compress old files instead of deletion
4. **Backup Integration**: Archive files before deletion
5. **Advanced Scheduling**: Configurable cleanup intervals
6. **Notification System**: Alert on cleanup operations

## Conclusion

The audio cleanup implementation provides a robust, configurable, and user-friendly solution for managing disk space usage in the Discord bot. It integrates seamlessly with the existing architecture while providing comprehensive monitoring and control capabilities.

The system is production-ready and includes all necessary safety features, error handling, and monitoring capabilities to ensure reliable operation in various environments.
