#!/usr/bin/env python3
"""
Health check script for Discord Voice Channel Join Monitor Bot
This script performs comprehensive health checks and can restart the bot if needed.
"""

import requests
import time
import json
import sys
import os
import subprocess
import logging
from datetime import datetime

# Configuration
HEALTH_CHECK_URL = "http://localhost:8080/status"
TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 5
CONTAINER_NAME = "discord-tanga-bot"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/data/logs/health_check.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def check_web_server():
    """Check if the web server is responding"""
    try:
        response = requests.get(HEALTH_CHECK_URL, timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if data.get('success', False):
                logger.info("✅ Web server is healthy")
                return True
            else:
                logger.warning(f"⚠️ Web server responded but with error: {data.get('error', 'Unknown error')}")
                return False
        else:
            logger.warning(f"⚠️ Web server returned status code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Web server health check failed: {e}")
        return False

def check_discord_connection():
    """Check if the bot is connected to Discord"""
    try:
        response = requests.get(HEALTH_CHECK_URL, timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            status = data.get('status', {})
            online_users = status.get('online_users', 0)
            # If we can get online users count, the Discord connection is likely working
            logger.info(f"✅ Discord connection appears healthy (online users: {online_users})")
            return True
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Discord connection check failed: {e}")
        return False

def restart_container():
    """Restart the Docker container"""
    try:
        logger.info("🔄 Attempting to restart container...")
        result = subprocess.run(
            ["docker", "restart", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            logger.info("✅ Container restarted successfully")
            return True
        else:
            logger.error(f"❌ Failed to restart container: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("❌ Container restart timed out")
        return False
    except Exception as e:
        logger.error(f"❌ Error restarting container: {e}")
        return False

def get_container_status():
    """Get the current status of the container"""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format={{.State.Status}}", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.error(f"❌ Failed to get container status: {result.stderr}")
            return "unknown"
    except Exception as e:
        logger.error(f"❌ Error getting container status: {e}")
        return "unknown"

def comprehensive_health_check():
    """Perform a comprehensive health check"""
    logger.info("🔍 Starting comprehensive health check...")
    
    # Check container status first
    container_status = get_container_status()
    logger.info(f"📦 Container status: {container_status}")
    
    if container_status not in ["running", "healthy"]:
        logger.warning(f"⚠️ Container is not in a healthy state: {container_status}")
        return False
    
    # Check web server
    web_healthy = check_web_server()
    
    # Check Discord connection
    discord_healthy = check_discord_connection()
    
    # Overall health assessment
    overall_healthy = web_healthy and discord_healthy
    
    if overall_healthy:
        logger.info("✅ All health checks passed")
    else:
        logger.warning("⚠️ Some health checks failed")
    
    return overall_healthy

def main():
    """Main health check function"""
    logger.info("🚀 Starting Discord Bot Health Check")
    
    # Perform health check
    is_healthy = comprehensive_health_check()
    
    if not is_healthy:
        logger.warning("⚠️ Health check failed, attempting restart...")
        
        # Wait a bit before restarting to avoid rapid restarts
        time.sleep(RETRY_DELAY)
        
        # Attempt restart
        restart_success = restart_container()
        
        if restart_success:
            logger.info("✅ Restart initiated successfully")
            # Wait for container to start up
            time.sleep(30)
            
            # Verify restart was successful
            final_check = comprehensive_health_check()
            if final_check:
                logger.info("✅ Bot is healthy after restart")
                sys.exit(0)
            else:
                logger.error("❌ Bot is still unhealthy after restart")
                sys.exit(1)
        else:
            logger.error("❌ Failed to restart container")
            sys.exit(1)
    else:
        logger.info("✅ Bot is healthy, no action needed")
        sys.exit(0)

if __name__ == "__main__":
    main()
