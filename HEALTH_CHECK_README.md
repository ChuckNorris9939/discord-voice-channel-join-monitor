# Discord Bot Health Check System

This document explains the health check system implemented for the Discord Voice Channel Join Monitor Bot to automatically restart the bot when it becomes unresponsive.

## Overview

The health check system includes multiple layers of monitoring:

1. **Docker Health Check** - Built into the container
2. **Docker Compose Health Check** - Container orchestration level
3. **External Health Monitor** - Standalone monitoring service
4. **Manual Health Check Script** - For troubleshooting

## Features

- ✅ Automatic container restart on health check failure
- ✅ Web server endpoint monitoring (`/status`)
- ✅ Discord connection verification
- ✅ Configurable retry policies and timeouts
- ✅ Comprehensive logging
- ✅ Multiple monitoring approaches

## Quick Start

### Option 1: Basic Docker Compose (Recommended)

```bash
# Use the updated docker-compose.yml with built-in health checks
docker-compose up -d

# Check health status
docker ps  # Look for "healthy" status
```

### Option 2: Advanced Monitoring

```bash
# Use the enhanced docker-compose with external health monitor
docker-compose -f docker-compose.health.yml up -d

# Monitor logs
docker logs discord-bot-health-monitor
```

### Option 3: External Monitoring

```bash
# Run external monitoring script (outside Docker)
./monitor_bot.sh
```

## Health Check Configuration

### Docker Health Check Parameters

- **Interval**: 30 seconds (how often to check)
- **Timeout**: 10 seconds (max time to wait for response)
- **Retries**: 3 attempts before marking as unhealthy
- **Start Period**: 60 seconds (grace period after container start)

### Restart Policy

- **Condition**: `on-failure` (restart only on failure)
- **Delay**: 5 seconds (wait before restart)
- **Max Attempts**: 3 (max restarts in window)
- **Window**: 120 seconds (time window for max attempts)

## Monitoring Endpoints

### Health Check Endpoint

- **URL**: `http://localhost:8083/status`
- **Method**: GET
- **Response**: JSON with bot status information

Example response:
```json
{
  "success": true,
  "status": {
    "voice_events": 42,
    "recordings": 15,
    "online_users": 3,
    "garmin_health": {
      "autojoin": true,
      "recording": false,
      "duration": "0.0s",
      "buffer": "0.00 MB",
      "errors": "0/5",
      "processing": "Idle"
    }
  }
}
```

## Log Files

Health check logs are stored in:
- Container logs: `docker logs discord-tanga-bot`
- Health monitor logs: `./data/logs/health_check.log`
- External monitor logs: `./data/logs/monitor.log`

## Troubleshooting

### Check Container Health

```bash
# View container health status
docker inspect discord-tanga-bot --format='{{.State.Health.Status}}'

# View health check logs
docker inspect discord-tanga-bot --format='{{range .State.Health.Log}}{{.Output}}{{end}}'
```

### Manual Health Check

```bash
# Run health check manually
docker exec discord-tanga-bot python3 health_check.py

# Or from outside the container
curl -f http://localhost:8083/status
```

### Force Restart

```bash
# Restart the container manually
docker restart discord-tanga-bot

# Or using docker-compose
docker-compose restart dc_voice_monitor
```

## Customization

### Adjust Health Check Intervals

Edit `docker-compose.yml`:
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/status"]
  interval: 30s      # Check every 30 seconds
  timeout: 10s       # 10 second timeout
  retries: 3         # 3 retries before marking unhealthy
  start_period: 60s  # 60 second grace period
```

### Modify Restart Policy

```yaml
deploy:
  restart_policy:
    condition: on-failure
    delay: 5s
    max_attempts: 3
    window: 120s
```

## Files Added/Modified

- `Dockerfile` - Added health check and curl installation
- `docker-compose.yml` - Added health check configuration
- `docker-compose.health.yml` - Advanced monitoring setup
- `health_check.py` - Comprehensive health check script
- `health_check.sh` - Health check wrapper script
- `monitor_bot.sh` - External monitoring script

## Notes

- The health check uses the existing `/status` endpoint in the Flask web server
- All health checks are logged for debugging purposes
- The system is designed to be resilient with multiple fallback mechanisms
- Health checks run independently of the main bot process
