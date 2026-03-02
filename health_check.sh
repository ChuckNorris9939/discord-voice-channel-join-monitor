#!/bin/bash
# Health check wrapper script for Discord Bot

# Set script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/data/logs"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Run the health check
cd "$SCRIPT_DIR"
python3 health_check.py

# Exit with the same code as the Python script
exit $?
