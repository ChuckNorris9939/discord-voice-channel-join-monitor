#!/usr/bin/env python3
"""
Test script to run only the Flask web interface without starting the Discord bot.
This allows you to test the web pages independently.

Usage:
    python test_web_interface.py

The web interface will be available at: http://localhost:5000
"""

import os
import sys
import sqlite3
from pathlib import Path
import time

# Add the current directory to Python path so we can import from main.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import Flask app and related functions from main.py
from main import app, DATABASE_PATH, GARMIN_OUTPUT_DIR, BOT_VERSION, BOT_START_TIME

def create_test_data():
    """Create some test data for the web interface if database doesn't exist."""
    try:
        # Create config directory if it doesn't exist
        config_dir = os.path.dirname(DATABASE_PATH)
        os.makedirs(config_dir, exist_ok=True)
        
        # Check if database exists
        if not os.path.exists(DATABASE_PATH):
            print("📊 Creating test database with sample data...")
            
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            # Create the user_voice_events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_voice_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    channel_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Insert some test data
            test_data = [
                (123456789, "TestUser1", 987654321, "General", "join"),
                (234567890, "TestUser2", 987654321, "General", "join"),
                (345678901, "TestUser3", 876543210, "Music", "join"),
                (456789012, "TestUser4", 765432109, "Gaming", "join"),
                (567890123, "TestUser5", 654321098, "General", "join"),
            ]
            
            cursor.executemany('''
                INSERT INTO user_voice_events (user_id, username, channel_id, channel_name, event_type)
                VALUES (?, ?, ?, ?, ?)
            ''', test_data)
            
            conn.commit()
            conn.close()
            print("✅ Test database created with sample data")
        else:
            print("✅ Database already exists")
            
    except Exception as e:
        print(f"⚠️ Warning: Could not create test data: {e}")

def create_test_recordings():
            """Create test recording files if data/garmin-output directory is empty."""
    try:
        # Create data/garmin-output directory if it doesn't exist
        os.makedirs(GARMIN_OUTPUT_DIR, exist_ok=True)
        
        # Check if directory is empty
        if not os.listdir(GARMIN_OUTPUT_DIR):
            print("🎙️ Creating test recording files...")
            
            # Create some dummy .wav files for testing
            test_files = [
                "test_recording_1.wav",
                "test_recording_2.wav", 
                "test_recording_3.wav"
            ]
            
            for filename in test_files:
                filepath = os.path.join(GARMIN_OUTPUT_DIR, filename)
                with open(filepath, 'w') as f:
                    f.write("This is a test WAV file content")
                print(f"   Created: {filename}")
            
            print("✅ Test recording files created")
        else:
            print("✅ Garmin output directory already contains files")
            
    except Exception as e:
        print(f"⚠️ Warning: Could not create test recordings: {e}")

def main():
    """Main function to run the Flask web interface in test mode."""
    print("🤖 Discord Bot Web Interface Test Mode")
    print("=" * 50)
    
    # Create test data
    create_test_data()
    create_test_recordings()
    
    print("\n🌐 Starting Flask web interface...")
    print("📱 Web interface will be available at: http://localhost:8081")
    print("🛑 Press Ctrl+C to stop the server")
    print("=" * 50)
    
    try:
        # Run Flask in debug mode for development
        app.run(
            host='0.0.0.0',  # Allow external connections
            port=8081,       # Custom port for testing
            debug=True,      # Enable debug mode for development
            use_reloader=False  # Disable reloader to avoid issues
        )
    except KeyboardInterrupt:
        print("\n🛑 Web interface stopped by user")
    except Exception as e:
        print(f"❌ Error running web interface: {e}")

if __name__ == "__main__":
    main() 