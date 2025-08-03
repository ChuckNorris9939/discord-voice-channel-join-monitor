# Individual User Recordings Feature

## Overview

The Discord Voice Bot now supports saving individual user recordings before mixing the audio. This feature allows you to have separate audio files for each user in addition to the mixed recording, providing better control over audio analysis and editing.

## Feature Description

When a recording is saved, the system now creates:

1. **Individual User Recordings**: Separate WAV files for each user's audio
2. **Mixed Recording**: The combined audio file with all users (existing functionality)

### File Naming Convention

- **Mixed Recording**: `recording_DD.MM.YYYY_HH-MM.wav`
- **Individual User Recording**: `recording_DD.MM.YYYY_HH-MM_user_USERID.wav`

**Example**:
```
recording_03.08.2025_08-53.wav                    # Mixed recording
recording_03.08.2025_08-53_user_123456.wav       # User 123456's audio
recording_03.08.2025_08-53_user_789012.wav       # User 789012's audio
recording_03.08.2025_08-53_user_345678.wav       # User 345678's audio
```

## Technical Implementation

### Core Changes

#### 1. New Method: `_save_individual_user_recordings`

**File**: `garmin_voice.py`

**Purpose**: Saves individual user recordings before mixing

**Key Features**:
- Extracts synchronized audio data for each user
- Sorts frames by timestamp to ensure proper chronological order
- Creates separate WAV files for each user
- Handles errors gracefully for individual users

**Implementation**:
```python
def _save_individual_user_recordings(self, base_filename: str) -> dict[int, str]:
    """Save individual user recordings before mixing.
    
    Args:
        base_filename: Base filename without extension (e.g., "recording_03.08.2025_08-53")
        
    Returns:
        Dictionary mapping user_id to saved file path
    """
```

#### 2. Modified `save_recording` Method

**File**: `garmin_voice.py`

**Changes**:
- Calls `_save_individual_user_recordings` before mixing
- Updated logging to include individual file count
- Maintains backward compatibility

**Before**:
```python
# Combine per-user audio streams
combined_audio = self._combine_user_audio_streams()
```

**After**:
```python
# Save individual user recordings first
individual_files = self._save_individual_user_recordings(base_filename)

# Combine per-user audio streams for mixed recording
combined_audio = self._combine_user_audio_streams()
```

### Web Interface Updates

#### 1. Enhanced Recording Display

**File**: `main.py` - `garmin_recordings_page` route

**Features**:
- Groups recordings by base filename
- Distinguishes between mixed and individual recordings
- Shows file count and modification time for each group

#### 2. Updated HTML Template

**File**: `templates/garmin_recordings.html`

**Features**:
- Hierarchical display with recording groups
- Color-coded badges for mixed vs individual recordings
- Improved layout for multiple files per session
- Responsive design for mobile devices

**Visual Elements**:
- **Mixed Recording**: Blue badge with "Mixed" label
- **Individual Recording**: Orange badge with "User" label
- **Group Headers**: Show base filename and file count
- **Sub-items**: Individual files with audio controls and download buttons

## Benefits

### 1. Audio Analysis
- **Isolated Analysis**: Analyze each user's speech patterns separately
- **Quality Assessment**: Identify which users have audio quality issues
- **Content Review**: Review individual contributions without background noise

### 2. Editing and Post-Processing
- **Selective Editing**: Edit or remove specific user audio
- **Volume Adjustment**: Adjust individual user volumes independently
- **Timeline Analysis**: Compare speaking patterns and timing

### 3. Troubleshooting
- **Debug Audio Issues**: Identify which user has problematic audio
- **Network Analysis**: Determine if audio issues are user-specific or global
- **Performance Monitoring**: Track individual user audio quality over time

### 4. Content Management
- **User-Specific Content**: Create user-specific audio clips
- **Meeting Minutes**: Correlate audio with user contributions
- **Training Data**: Use individual recordings for speech recognition training

## Usage

### Automatic Operation

The feature works automatically - no configuration required:

1. **Start Recording**: Use `/garmin_start` or web interface
2. **Stop Recording**: Use `/garmin_stop` or web interface  
3. **Save Recording**: Use `/garmin_save` or web interface
4. **Access Files**: View and download from web interface

### Web Interface

1. Navigate to **Garmin Recordings** page
2. View grouped recordings by session
3. Play individual user recordings or mixed recording
4. Download specific files as needed

### File Management

**Location**: `data/garmin-output/`

**File Types**:
- `.wav` files (uncompressed audio)
- Compatible with all audio editing software
- Standard WAV format (48kHz, 16-bit, stereo)

## Technical Details

### Audio Format
- **Sample Rate**: 48,000 Hz
- **Bit Depth**: 16-bit
- **Channels**: 2 (stereo)
- **Format**: WAV (uncompressed)

### Synchronization
- Uses the same timestamp-based synchronization as mixed recordings
- Ensures individual recordings maintain proper timing
- Frames are sorted chronologically before saving

### Error Handling
- Individual user failures don't affect other users
- Graceful degradation if one user's audio is corrupted
- Comprehensive logging for troubleshooting

### Performance Impact
- **Minimal Overhead**: Uses existing synchronized buffers
- **No Additional Processing**: Extracts data already in memory
- **Efficient File I/O**: Writes files sequentially

## Configuration

### Environment Variables

No new environment variables required. Uses existing settings:

- `OUTPUT_DIR`: Directory for saving recordings
- Audio format constants in `garmin_voice.py`

### Storage Considerations

**File Size Impact**:
- Individual recordings may be smaller than mixed recording
- Total storage increases by approximately N-1 files per session
- Example: 3 users = 1 mixed + 3 individual = 4 files total

**Storage Management**:
- Consider implementing automatic cleanup for old recordings
- Monitor disk space usage
- Archive important recordings to external storage

## Troubleshooting

### Common Issues

#### 1. Missing Individual Recordings
**Symptoms**: Mixed recording exists but no individual files
**Causes**: 
- User had no audio data in buffer
- File write permissions issue
- Disk space full

**Solutions**:
- Check logs for error messages
- Verify disk space availability
- Ensure write permissions to output directory

#### 2. Individual Recording Quality Issues
**Symptoms**: Individual recordings sound different from mixed
**Causes**:
- Frame synchronization issues
- Audio buffer corruption

**Solutions**:
- Check `SYNC_BUFFER_SIZE` and `SYNC_TOLERANCE_MS` settings
- Review audio synchronization logs
- Test with different buffer settings

#### 3. Web Interface Display Issues
**Symptoms**: Individual recordings not showing in web interface
**Causes**:
- File naming convention mismatch
- Web interface cache issues

**Solutions**:
- Refresh web interface
- Check file naming follows convention
- Verify file permissions

### Log Analysis

**Key Log Messages**:
```
Saved individual recording for user 123456: path/to/file.wav (duration: 5.0s)
Saved 3 individual user recordings
Mixed recording saved: path/to/mixed.wav (individual files: 3)
```

**Error Messages**:
```
Error saving individual recording for user 123456: [error details]
No synchronized audio data found for individual user recordings
```

## Future Enhancements

### Potential Improvements

1. **Compression Options**: Support for compressed formats (Opus, MP3)
2. **User Identification**: Map Discord usernames to user IDs
3. **Selective Recording**: Option to record only specific users
4. **Audio Metadata**: Embed user information in WAV file headers
5. **Batch Operations**: Bulk download or processing of individual recordings

### Integration Possibilities

1. **Speech Recognition**: Process individual recordings for better accuracy
2. **Audio Analysis**: Integrate with audio analysis tools
3. **Cloud Storage**: Automatic upload to cloud storage services
4. **API Access**: REST API for programmatic access to recordings

## Conclusion

The Individual User Recordings feature provides significant value for audio analysis, editing, and troubleshooting while maintaining full backward compatibility. The implementation is efficient, robust, and user-friendly, making it easy to access and manage individual user audio data alongside the traditional mixed recordings. 