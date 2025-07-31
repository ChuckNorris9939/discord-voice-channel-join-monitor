# Garmin Voice Recording Configuration

This document describes all configurable settings for the Garmin voice recording system to prevent audio stuttering and optimize performance.

## Environment Variables

> **Note**: These environment variables can be set in a `.env` file for easier configuration. Copy `env.example` to `.env` and modify the values as needed.

### Audio Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `GARMIN_RECORD_SECONDS` | `600` | Maximum recording duration in seconds (10 minutes) |
| `GARMIN_SAMPLERATE` | `48000` | Audio sample rate (Discord standard) |
| `GARMIN_CHANNELS` | `2` | Number of audio channels (stereo) |
| `GARMIN_BYTES_PER_SAMPLE` | `2` | Bytes per sample (16-bit) |
| `GARMIN_FRAMES_PER_BUFFER` | `960` | Frames per buffer (20ms @ 48kHz) |

### Audio Pipeline Health Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `GARMIN_AUDIO_CALLBACK_TIMEOUT` | `5.0` | Maximum seconds between audio callbacks before restart |
| `GARMIN_MIN_AUDIO_CHUNK` | `1920` | Minimum expected audio chunk size in bytes |
| `GARMIN_MAX_AUDIO_CHUNK` | `9600` | Maximum expected audio chunk size in bytes |

### Recording Management

| Variable | Default | Description |
|----------|---------|-------------|
| `GARMIN_RECORDING_RESTART_DELAY` | `1.0` | Delay in seconds before restarting recording after save |
| `GARMIN_MAX_RECORDING_DURATION` | `3600` | Maximum recording duration before auto-restart (1 hour) |
| `GARMIN_BUFFER_MONITOR_INTERVAL` | `30.0` | Interval in seconds for buffer health monitoring |
| `GARMIN_MAX_RECORDING_ERRORS` | `5` | Maximum errors before auto-restart |

### Speech-to-Text Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `STT_ENGINE` | `google` | STT engine: `google` or `vosk` |
| `VOSK_MODEL_PATH` | `vosk-model-de` | Path to Vosk model for offline STT |

## Troubleshooting Audio Stuttering

### Common Causes and Solutions

1. **Buffer Overflow**
   - **Symptom**: Audio cuts out or becomes choppy
   - **Solution**: Increase `GARMIN_FRAMES_PER_BUFFER` or reduce `GARMIN_RECORD_SECONDS`
   - **Example**: `GARMIN_FRAMES_PER_BUFFER=1920` (40ms buffer)

2. **Network Latency**
   - **Symptom**: Delayed or missing audio segments
   - **Solution**: Increase buffer size and monitoring interval
   - **Example**: `GARMIN_BUFFER_MONITOR_INTERVAL=60.0`

3. **Memory Pressure**
   - **Symptom**: System becomes unresponsive during recording
   - **Solution**: Reduce `GARMIN_RECORD_SECONDS` or `GARMIN_MAX_RECORDING_DURATION`
   - **Example**: `GARMIN_RECORD_SECONDS=300` (5 minutes)

4. **STT Processing Bottleneck**
   - **Symptom**: Audio continues but STT recognition fails
   - **Solution**: Switch to offline Vosk engine or increase error tolerance
   - **Example**: `STT_ENGINE=vosk` and `GARMIN_MAX_RECORDING_ERRORS=10`

### Performance Optimization

1. **For Low-End Systems**
   ```bash
   # In your .env file:
   GARMIN_RECORD_SECONDS=300
   GARMIN_FRAMES_PER_BUFFER=1920
   GARMIN_BUFFER_MONITOR_INTERVAL=60.0
   GARMIN_MAX_RECORDING_ERRORS=3
   ```

2. **For High-Performance Systems**
   ```bash
   # In your .env file:
   GARMIN_RECORD_SECONDS=1200
   GARMIN_FRAMES_PER_BUFFER=480
   GARMIN_BUFFER_MONITOR_INTERVAL=15.0
   GARMIN_MAX_RECORDING_ERRORS=10
   ```

3. **For Network-Unstable Environments**
   ```bash
   # In your .env file:
   GARMIN_RECORDING_RESTART_DELAY=2.0
   GARMIN_BUFFER_MONITOR_INTERVAL=45.0
   GARMIN_MAX_RECORDING_ERRORS=8
   ```

## Health Monitoring

### Using the Health Check Command

Use `/garmin_health` to monitor the recording system status:

- **Connection Status**: Shows if the bot is connected to voice channel
- **Recording Duration**: Current recording session length
- **Buffer Size**: Current audio buffer size in MB
- **Recording Errors**: Number of errors vs. maximum allowed
- **Processing Status**: Whether STT processing is active
- **STT Engine**: Which speech recognition engine is being used
- **Audio Pipeline Health**: Whether the audio pipeline is healthy
- **Audio Callback Errors**: Number of audio callback errors
- **Time Since Last Audio**: Seconds since last audio callback
- **Audio Callback Rate**: Callbacks per second (should be ~50)
- **Last Chunk Size**: Size of the last audio chunk received

### Automatic Recovery

The system automatically restarts recording when:

1. **Max Duration Exceeded**: Recording runs longer than `GARMIN_MAX_RECORDING_DURATION`
2. **Buffer Overflow**: Audio buffer exceeds maximum size
3. **Too Many Errors**: Error count reaches `GARMIN_MAX_RECORDING_ERRORS`
4. **Empty Buffer**: No audio data received for 60+ seconds

### Manual Recovery

If automatic recovery fails:

1. Use `/garmin_stop` to disconnect
2. Wait 5-10 seconds
3. Use `/garmin_start` to reconnect
4. Check health with `/garmin_health`

## Bot Commands

| Command | Description |
|---------|-------------|
| `/garmin_start` | Start voice recording in current channel |
| `/garmin_stop` | Stop voice recording and disconnect |
| `/garmin_save` | Save current recording and restart |
| `/garmin_health` | Show recording system health status |

## Logging

Enable debug logging to troubleshoot issues:

```bash
export LOG_LEVEL=DEBUG
export APP_TESTING_MODE=true
```

Key log messages to watch for:

- `"Recording health check failed"` - Indicates automatic restart
- `"Error during save_recording"` - Save operation failed
- `"Google STT request error"` - Speech recognition failed
- `"Buffer monitoring task cancelled"` - Normal shutdown

## Advanced Configuration

### Custom Buffer Sizes

For specific use cases, you can calculate optimal buffer sizes:

```python
# Calculate buffer size for 30 seconds at 48kHz stereo 16-bit
buffer_seconds = 30
sample_rate = 48000
channels = 2
bytes_per_sample = 2
buffer_size = buffer_seconds * sample_rate * channels * bytes_per_sample
# Result: 5,760,000 bytes (5.76 MB)
```

### STT Engine Comparison

| Engine | Pros | Cons | Best For |
|--------|------|------|----------|
| Google | High accuracy, no setup | Requires internet, rate limits | General use |
| Vosk | Offline, no rate limits | Lower accuracy, large model | Privacy-focused |

## Troubleshooting Checklist

- [ ] Check network connection stability
- [ ] Verify Discord voice permissions
- [ ] Monitor system memory usage
- [ ] Check STT engine status
- [ ] Review error logs
- [ ] Test with different buffer settings
- [ ] Verify audio file output
- [ ] Check bot permissions in voice channel

## Support

If issues persist after trying these solutions:

1. Check the bot logs for specific error messages
2. Try different environment variable combinations
3. Test in a different voice channel
4. Verify Discord API status
5. Consider switching STT engines 