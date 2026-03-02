#!/bin/bash
# External monitoring script for Discord Bot
# This script can be run from outside Docker to monitor the bot

CONTAINER_NAME="discord-tanga-bot"
HEALTH_URL="http://localhost:8083/status"
LOG_FILE="./data/logs/monitor.log"
CHECK_INTERVAL=60  # Check every 60 seconds

# Create logs directory if it doesn't exist
mkdir -p "$(dirname "$LOG_FILE")"

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

check_bot_health() {
    # Check if container is running
    if ! docker ps --format "table {{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
        log_message "❌ Container $CONTAINER_NAME is not running"
        return 1
    fi
    
    # Check health endpoint
    if curl -f -s "$HEALTH_URL" > /dev/null 2>&1; then
        log_message "✅ Bot is healthy"
        return 0
    else
        log_message "❌ Bot health check failed"
        return 1
    fi
}

restart_bot() {
    log_message "🔄 Restarting bot container..."
    if docker restart "$CONTAINER_NAME" > /dev/null 2>&1; then
        log_message "✅ Bot container restarted successfully"
        # Wait for bot to start up
        sleep 30
        return 0
    else
        log_message "❌ Failed to restart bot container"
        return 1
    fi
}

main() {
    log_message "🚀 Starting Discord Bot Monitor"
    
    while true; do
        if ! check_bot_health; then
            log_message "⚠️ Bot is unhealthy, attempting restart..."
            if restart_bot; then
                # Verify restart was successful
                sleep 30
                if check_bot_health; then
                    log_message "✅ Bot is healthy after restart"
                else
                    log_message "❌ Bot is still unhealthy after restart"
                fi
            fi
        fi
        
        sleep "$CHECK_INTERVAL"
    done
}

# Handle script interruption
trap 'log_message "🛑 Monitor stopped"; exit 0' INT TERM

main
