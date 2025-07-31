# Audio Stuttering Troubleshooting Guide

## Understanding Audio Stuttering

Audio stuttering in Discord voice recordings can manifest as:
- **Dropouts**: Complete silence gaps in the recording
- **Jitter**: Irregular timing causing choppy audio
- **Buffer underruns**: Not enough audio data available
- **Network issues**: Discord connection problems

## Quick Diagnostic Steps

### 1. Check Current Settings
Use the health check command to see your current status:
```
/garmin_health
```

Look for:
- Buffer size (should be > 0 and stable)
- Recording errors (should be low)
- Processing status (should not be stuck)

### 2. Enable Debug Logging
Set these environment variables to get detailed logs:
```bash
LOG_LEVEL=DEBUG
APP_TESTING_MODE=true
```

## Configuration Solutions

### Solution 1: Buffer Underrun Fix (Based on Your Spectrogram)
**This is specifically for your stuttering pattern with 1-2 second gaps and sharp vertical lines.**

Add these to your `.env` file:
```bash
# CRITICAL: Fix for buffer underrun stuttering
GARMIN_FRAMES_PER_BUFFER=1920
GARMIN_PROCESS_INTERVAL=1.0
GARMIN_BUFFER_PREFILL=192000
GARMIN_BUFFER_OVERFLOW=0.7
LOG_LEVEL=DEBUG
APP_TESTING_MODE=true
```

**Why this fixes your specific issue:**
- `GARMIN_FRAMES_PER_BUFFER=1920`: Larger buffer prevents underruns
- `GARMIN_BUFFER_PREFILL=192000`: Ensures 4 seconds of audio before processing
- `GARMIN_PROCESS_INTERVAL=1.0`: Less frequent processing reduces buffer drain
- `GARMIN_BUFFER_OVERFLOW=0.7`: More conservative buffer management

### Solution 2: Conservative Buffer Settings (Alternative)
Add these to your `.env` file:
```bash
# Conservative settings for stability
GARMIN_FRAMES_PER_BUFFER=1920
GARMIN_PROCESS_INTERVAL=1.0
GARMIN_BUFFER_PREFILL=192000
GARMIN_BUFFER_OVERFLOW=0.7
GARMIN_WINDOW_MIN=96000
GARMIN_WINDOW_MAX=480000
```

### Solution 2: Aggressive Buffer Settings (For High-Performance Systems)
```bash
# Aggressive settings for minimal latency
GARMIN_FRAMES_PER_BUFFER=480
GARMIN_PROCESS_INTERVAL=0.2
GARMIN_BUFFER_PREFILL=48000
GARMIN_BUFFER_OVERFLOW=0.9
GARMIN_WINDOW_MIN=24000
GARMIN_WINDOW_MAX=120000
```

### Solution 3: Network-Unstable Environment
```bash
# Settings for poor network conditions
GARMIN_FRAMES_PER_BUFFER=2400
GARMIN_PROCESS_INTERVAL=2.0
GARMIN_BUFFER_PREFILL=240000
GARMIN_BUFFER_OVERFLOW=0.6
GARMIN_RECORDING_RESTART_DELAY=2.0
GARMIN_BUFFER_MONITOR_INTERVAL=15.0
```

## Advanced Troubleshooting

### 1. Monitor Buffer Health
Watch the debug logs for these patterns:
```
[DEBUG] Processing audio window: X bytes (Y.YYs)
[DEBUG] Buffer overflow prevented: removed X bytes
[WARNING] Recording health check failed: buffer overflow
```

### 2. Check System Resources
- **CPU Usage**: High CPU can cause processing delays
- **Memory**: Insufficient RAM can cause buffer issues
- **Network**: Unstable internet connection
- **Discord Server**: Server-side issues

### 3. Test Different STT Engines
Try switching between Google and Vosk:
```bash
# For Google STT (requires internet)
STT_ENGINE=google

# For Vosk STT (offline, may be more stable)
STT_ENGINE=vosk
VOSK_MODEL_PATH=vosk-model-de
```

## Environment-Specific Solutions

### Windows
```bash
# Windows-specific optimizations
GARMIN_FRAMES_PER_BUFFER=1440
GARMIN_PROCESS_INTERVAL=0.8
GARMIN_BUFFER_PREFILL=144000
```

### Linux
```bash
# Linux-specific optimizations
GARMIN_FRAMES_PER_BUFFER=960
GARMIN_PROCESS_INTERVAL=0.5
GARMIN_BUFFER_PREFILL=96000
```

### Docker
```bash
# Docker-specific optimizations
GARMIN_FRAMES_PER_BUFFER=1920
GARMIN_PROCESS_INTERVAL=1.0
GARMIN_BUFFER_PREFILL=192000
```

## Testing Your Changes

1. **Restart the bot** after changing settings
2. **Join a voice channel** and start recording
3. **Monitor logs** for any errors or warnings
4. **Test with actual speech** to trigger STT processing
5. **Check the health** with `/garmin_health`

## When to Contact Support

Contact support if you see:
- Persistent buffer overflow errors
- High error counts (>10) in health check
- Complete audio dropouts lasting >5 seconds
- System crashes during recording

## Performance Monitoring

Use these commands to monitor performance:
```bash
# Check recording health
/garmin_health

# Save a test recording
/garmin_save

# Monitor logs for buffer issues
tail -f bot.log | grep -E "(buffer|stutter|error)"
```

## Common Misconfigurations

❌ **Too small buffer**: `GARMIN_FRAMES_PER_BUFFER=240`
✅ **Better**: `GARMIN_FRAMES_PER_BUFFER=960` or higher

❌ **Too frequent processing**: `GARMIN_PROCESS_INTERVAL=0.1`
✅ **Better**: `GARMIN_PROCESS_INTERVAL=0.5` or higher

❌ **Too large window**: `GARMIN_WINDOW_MAX=1000000`
✅ **Better**: `GARMIN_WINDOW_MAX=240000`

❌ **No buffer prefill**: `GARMIN_BUFFER_PREFILL=0`
✅ **Better**: `GARMIN_BUFFER_PREFILL=96000` (2 seconds of audio) 