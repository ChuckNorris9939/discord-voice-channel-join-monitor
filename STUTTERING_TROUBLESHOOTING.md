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

### Solution 1: Audio Pipeline Health Monitoring (Latest Fix)
**This addresses the core audio pipeline issues that persist even with STT disabled.**

Add these to your `.env` file:
```bash
# CRITICAL: Audio pipeline health monitoring
GARMIN_FRAMES_PER_BUFFER=1920
GARMIN_PROCESS_INTERVAL=3.0
GARMIN_BUFFER_PREFILL=384000
GARMIN_BUFFER_OVERFLOW=0.6
GARMIN_AUDIO_CALLBACK_TIMEOUT=5.0
GARMIN_MIN_AUDIO_CHUNK=1920
GARMIN_MAX_AUDIO_CHUNK=9600
LOG_LEVEL=DEBUG
APP_TESTING_MODE=true
```

**Why this addresses your persistent stuttering:**
- **Audio Pipeline Monitoring**: Detects and restarts on audio callback timeouts
- **Chunk Size Validation**: Ensures audio data integrity
- **Automatic Recovery**: Restarts recording when audio pipeline becomes unhealthy
- **Real-time Health Checks**: Monitors audio callback frequency and timing

### Solution 2: STT Processing Fix (Previous Solution)
**This was for STT-related stuttering, but your issue persists with STT disabled.**

Add these to your `.env` file:
```bash
# Previous fix for STT processing stuttering
GARMIN_FRAMES_PER_BUFFER=1920
GARMIN_PROCESS_INTERVAL=3.0
GARMIN_BUFFER_PREFILL=384000
GARMIN_BUFFER_OVERFLOW=0.6
LOG_LEVEL=DEBUG
APP_TESTING_MODE=true
```

**Why this was implemented:**
- `GARMIN_PROCESS_INTERVAL=3.0`: Much less frequent STT processing (was 1.0s)
- `GARMIN_BUFFER_PREFILL=384000`: Ensures 8 seconds of audio before processing
- `GARMIN_BUFFER_OVERFLOW=0.6`: More conservative buffer management
- **New**: Queue-based STT processing prevents threading conflicts

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

### 1. Monitor Audio Pipeline Health
Watch the debug logs for these patterns:
```
[DEBUG] Recording health OK - duration: X.Xs, buffer: X bytes, errors: X, audio: healthy, audio_errors: X, time_since_audio: X.Xs
[WARNING] Invalid audio chunk size: X bytes (expected 1920-9600)
[WARNING] Audio callback timeout: X.Xs since last callback
[WARNING] Recording health check failed: audio pipeline unhealthy
```

### 2. Monitor Buffer Health
Watch the debug logs for these patterns:
```
[DEBUG] Processing audio window: X bytes (Y.YYs)
[DEBUG] Buffer overflow prevented: removed X bytes
[WARNING] Recording health check failed: buffer overflow
```

### 3. Check System Resources
- **CPU Usage**: High CPU can cause processing delays
- **Memory**: Insufficient RAM can cause buffer issues
- **Network**: Unstable internet connection

### 4. Audio Pipeline Diagnostics
Use the health check to monitor audio pipeline health:
```
/garmin_health
```

Look for these key metrics:
- **audio_pipeline_healthy**: Should be `true`
- **audio_callback_errors**: Should be `0` or very low
- **time_since_last_audio**: Should be < 1 second during active recording
- **audio_callback_rate**: Should be ~50 callbacks/second (20ms intervals)
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

### 4. Disable STT to Test Audio Recording
To determine if stuttering is caused by STT processing:
```bash
# Disable STT completely
STT_ENABLED=false

# This will still record audio but skip all STT processing
# Use this to test if the issue persists without STT overhead
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

## Testing Without STT

To determine if stuttering is caused by STT processing:

1. **Set STT_ENABLED=false** in your `.env` file
2. **Restart the bot**
3. **Join a voice channel** - you should see "STT disabled" in logs
4. **Record audio** for several minutes
5. **Check spectrogram** - if stuttering is gone, the issue is STT-related
6. **Run `/garmin_health`** - should show "🔴 Disabled" for STT Status

**Expected Results:**
- ✅ **No STT processing logs** (no "Processing audio window" messages)
- ✅ **Clean audio recording** (if STT was the cause)
- ✅ **Health check shows STT disabled**
- ❌ **Stuttering persists** = issue is not STT-related

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