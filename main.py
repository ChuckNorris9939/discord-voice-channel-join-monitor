import os
from pathlib import Path
import threading
import datetime
import random
import sqlite3 # Added import for sqlite3
from flask import Flask
import discord
from discord import Intents, Thread, File, Embed
from discord.ext import commands, tasks
from typing import Dict, List, Optional
import signal
import asyncio
import time
import logging.handlers

# Import the audio cleanup service
from audio_cleanup_service import cleanup_service, run_cleanup, get_cleanup_stats

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env file loaded successfully")
except ImportError:
    print("⚠️ python-dotenv not installed. Install with: pip install python-dotenv")
    print("   Environment variables will only be loaded from system environment")
except Exception as e:
    print(f"⚠️ Error loading .env file: {e}")
    print("   Environment variables will only be loaded from system environment")

BOT_VERSION = "2.0.0"
# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
DATABASE_NAME = "user_log.db"
DATABASE_PATH = os.path.join(DATA_DIR, DATABASE_NAME)
GARMIN_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data", "garmin-output")
LOGS_DIR = os.path.join(SCRIPT_DIR, "data", "logs")

# --------- Logging ---------
import logging
import sys

# Create logs directory if it doesn't exist
os.makedirs(LOGS_DIR, exist_ok=True)

# Set up logging configuration with both console and file handlers
def setup_logging():
    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Create file handler with rotation (7 days retention)
    log_file = os.path.join(LOGS_DIR, "discord_bot.log")
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=7,  # Keep 7 days of logs
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Clean up old log files (older than 7 days)
    cleanup_old_logs()

def cleanup_old_logs():
    """Remove log files older than 7 days"""
    try:
        current_time = time.time()
        cutoff_time = current_time - (7 * 24 * 60 * 60)  # 7 days in seconds
        
        for filename in os.listdir(LOGS_DIR):
            if filename.endswith('.log'):
                file_path = os.path.join(LOGS_DIR, filename)
                file_time = os.path.getmtime(file_path)
                
                if file_time < cutoff_time:
                    try:
                        os.remove(file_path)
                        print(f"Removed old log file: {filename}")
                    except OSError as e:
                        print(f"Error removing old log file {filename}: {e}")
    except Exception as e:
        print(f"Error during log cleanup: {e}")

# Initialize logging
setup_logging()

logger = logging.getLogger("discord_bot") # Spezifischer Name für den Bot-Logger

# --------- Flask-Server für Health Checks ---------
from waitress import serve
from flask import Flask, render_template, url_for, request, send_from_directory, redirect # Ensure request is imported
import time
app = Flask(__name__, template_folder='templates')

# Track bot startup time for uptime calculation
BOT_START_TIME = time.time()

@app.route("/")
def home():
    # Calculate uptime
    uptime_seconds = int(time.time() - BOT_START_TIME)
    
    # Convert to human readable format
    if uptime_seconds < 60:
        uptime_str = f"{uptime_seconds}s"
    elif uptime_seconds < 3600:
        minutes = uptime_seconds // 60
        uptime_str = f"{minutes}m"
    elif uptime_seconds < 86400:
        hours = uptime_seconds // 3600
        uptime_str = f"{hours}h"
    else:
        days = uptime_seconds // 86400
        uptime_str = f"{days}d"
    
    # Get dynamic status data
    status_data = get_status_data()
    
    return render_template('home.html', 
                         app_testing_mode=TESTING, 
                         bot_version=BOT_VERSION, 
                         uptime=uptime_str,
                         status_data=status_data)

@app.route('/view_join_logs')
def view_join_logs_page():
    conn = None
    logs = []
    current_filter_username = request.args.get('username_filter', '').strip()
    try:
        # Use absolute path to ensure database is found regardless of working directory
        logger.info(f"Connecting to database at: {DATABASE_PATH}")
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        sql_query = "SELECT id, user_id, username, channel_id, channel_name, event_type, timestamp FROM user_voice_events"
        params = []

        if current_filter_username:
            sql_query += " WHERE username LIKE ?" # Use LIKE for partial matching
            params.append(f"%{current_filter_username}%")
        
        sql_query += " ORDER BY id DESC"
        
        cursor.execute(sql_query, params)
        logs = cursor.fetchall()
        logger.info(f"Successfully fetched {len(logs)} log entries for web interface. Filter: '{current_filter_username}'")
    except sqlite3.Error as e:
        logger.error(f"SQLite error when fetching logs for web interface (filter: '{current_filter_username}'): {e}")
    except Exception as e:
        logger.error(f"General error when fetching logs for web interface (filter: '{current_filter_username}'): {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
            logger.info(f"Database connection closed for /view_join_logs (filter: '{current_filter_username}').")
            
    return render_template('view_logs.html', logs=logs, current_filter_username=current_filter_username)

@app.route('/garmin_recordings')
def garmin_recordings_page():
    import os
    from pathlib import Path
    
    recordings = []
    # Use absolute path to ensure directory is found regardless of working directory
    output_dir = Path(GARMIN_OUTPUT_DIR)
    logger.info(f"Looking for recordings in: {output_dir}")
    
    if output_dir.exists():
        # Group recordings by base filename
        recording_groups = {}
        
        for file_path in output_dir.glob("*.mp3"):
            if file_path.is_file():
                # Get file stats
                stat = file_path.stat()
                file_size = stat.st_size
                modified_time = stat.st_mtime
                
                filename = file_path.name
                
                # Check if this is an individual user recording or mixed recording
                if "_user_" in filename:
                    # Individual user recording: "recording_03.08.2025_08-53_user_username.mp3"
                    base_filename = filename.replace("_user_", "_").rsplit("_", 1)[0]
                    username = filename.split("_user_")[1].replace(".mp3", "")
                    recording_type = "individual"
                    user_info = f"User {username}"
                else:
                    # Mixed recording: "recording_03.08.2025_08-53.mp3"
                    base_filename = filename.replace(".mp3", "")
                    username = None
                    recording_type = "mixed"
                    user_info = "Mixed (All Users)"
                
                recording_info = {
                    'filename': filename,
                    'size_mb': round(file_size / (1024 * 1024), 2),
                    'modified': time.strftime('%d.%m.%Y %H:%M', time.localtime(modified_time)),
                    'modified_timestamp': modified_time,
                    'path': str(file_path),
                    'recording_type': recording_type,
                    'user_info': user_info,
                    'username': username
                }
                
                if base_filename not in recording_groups:
                    recording_groups[base_filename] = {
                        'base_filename': base_filename,
                        'modified_timestamp': modified_time,
                        'recordings': []
                    }
                
                recording_groups[base_filename]['recordings'].append(recording_info)
                
                # Update the group's timestamp to the latest file
                if modified_time > recording_groups[base_filename]['modified_timestamp']:
                    recording_groups[base_filename]['modified_timestamp'] = modified_time
        
        # Convert groups to list and sort by timestamp (newest first)
        recordings = list(recording_groups.values())
        recordings.sort(key=lambda x: x['modified_timestamp'], reverse=True)
        
        # Sort recordings within each group (mixed first, then individual users)
        for group in recordings:
            group['recordings'].sort(key=lambda x: (x['recording_type'] != 'mixed', x['username'] or ''))
        
        logger.info(f"Found {len(recordings)} recording groups with {sum(len(g['recordings']) for g in recordings)} total files")
    else:
        logger.warning(f"Garmin output directory does not exist: {output_dir}")
    
    return render_template('garmin_recordings.html', recordings=recordings)

@app.route('/download_recording/<filename>')
def download_recording(filename):
    """Download a specific recording file."""
    import os
    from pathlib import Path
    
    # Use absolute path to ensure directory is found regardless of working directory
    output_dir = Path(GARMIN_OUTPUT_DIR)
    file_path = output_dir / filename
    
    if file_path.exists() and file_path.is_file():
        return send_from_directory(output_dir, filename, as_attachment=True)
    else:
        logger.warning(f"Recording file not found: {file_path}")
        return "File not found", 404

@app.route('/settings', methods=['GET', 'POST'])
def settings_route():
    message = None 
    error = None   

    if request.method == 'POST':
        logger.info("Settings page: POST request received.")
        try:
            # Handle APP_TESTING_MODE
            app_testing_mode_str = request.form.get('app_testing_mode')
            if app_testing_mode_str in ['true', 'false']:
                save_setting(DB_KEY_APP_TESTING_MODE, app_testing_mode_str)
                logger.info(f"Saved {DB_KEY_APP_TESTING_MODE}: {app_testing_mode_str}")

            # Handle HIDDEN_CHANNELS
            hidden_channels_str = request.form.get('hidden_channels', '')
            save_setting(DB_KEY_HIDDEN_CHANNELS, hidden_channels_str)
            logger.info(f"Saved {DB_KEY_HIDDEN_CHANNELS}: {hidden_channels_str}")

            # Handle LOG_CHANNEL_ID
            log_channel_id_str = request.form.get('log_channel_id', '')
            save_setting(DB_KEY_LOG_CHANNEL_ID, log_channel_id_str if log_channel_id_str else "None")
            logger.info(f"Saved {DB_KEY_LOG_CHANNEL_ID}: {log_channel_id_str}")
            
            # Handle BOT_AUDIT_ID
            bot_audit_id_str = request.form.get('bot_audit_id', '')
            save_setting(DB_KEY_BOT_AUDIT_ID, bot_audit_id_str if bot_audit_id_str else "None")
            logger.info(f"Saved {DB_KEY_BOT_AUDIT_ID}: {bot_audit_id_str}")

            # Handle TECHSUPPORT_CHANNEL_ID
            techsupport_channel_id_str = request.form.get('techsupport_channel_id', '')
            save_setting(DB_KEY_TECHSUPPORT_CHANNEL_ID, techsupport_channel_id_str if techsupport_channel_id_str else "None")
            logger.info(f"Saved {DB_KEY_TECHSUPPORT_CHANNEL_ID}: {techsupport_channel_id_str}")

            # Handle AFK_CHANNEL_ID
            afk_channel_id_str = request.form.get('afk_channel_id', '')
            save_setting(DB_KEY_AFK_CHANNEL_ID, afk_channel_id_str if afk_channel_id_str else "None")
            logger.info(f"Saved {DB_KEY_AFK_CHANNEL_ID}: {afk_channel_id_str}")

            # Handle PURGE_OLDER_THAN_DAYS
            purge_days_str = request.form.get('purge_older_than_days', '7')
            save_setting(DB_KEY_PURGE_OLDER_THAN_DAYS, purge_days_str)
            logger.info(f"Saved {DB_KEY_PURGE_OLDER_THAN_DAYS}: {purge_days_str}")

            # Handle JOIN_MESSAGE_TIMER_ENABLED
            join_timer_enabled_str = request.form.get('join_message_timer_enabled', 'true')
            save_setting(DB_KEY_JOIN_MESSAGE_TIMER_ENABLED, join_timer_enabled_str)
            logger.info(f"Saved {DB_KEY_JOIN_MESSAGE_TIMER_ENABLED}: {join_timer_enabled_str}")

            # Handle JOIN_MESSAGE_TIMER_MINUTES
            join_timer_minutes_str = request.form.get('join_message_timer_minutes', '7')
            save_setting(DB_KEY_JOIN_MESSAGE_TIMER_MINUTES, join_timer_minutes_str)
            logger.info(f"Saved {DB_KEY_JOIN_MESSAGE_TIMER_MINUTES}: {join_timer_minutes_str}")

            # Handle AFK_TIMER_MINUTES
            afk_timer_minutes_str = request.form.get('afk_timer_minutes', '10')
            save_setting(DB_KEY_AFK_TIMER_MINUTES, afk_timer_minutes_str)
            logger.info(f"Saved {DB_KEY_AFK_TIMER_MINUTES}: {afk_timer_minutes_str}")

            # Handle Garmin Recorder Settings
            save_setting(DB_KEY_STT_ENABLED, request.form.get('stt_enabled', 'true'))
            save_setting(DB_KEY_STT_ENGINE, request.form.get('stt_engine', 'google'))
            save_setting(DB_KEY_VOSK_MODEL_PATH, request.form.get('vosk_model_path', 'data/assets/models/vosk-model-small-de-0.15/'))
            save_setting(DB_KEY_GARMIN_AUTO_JOIN_ENABLED, request.form.get('garmin_auto_join_enabled', 'true'))
            save_setting(DB_KEY_GARMIN_AUTO_JOIN_CHANNELS, request.form.get('garmin_auto_join_channels', '1080202313211326584,571755941725208616,492036470681632778'))
            save_setting(DB_KEY_GARMIN_RECORD_SECONDS, request.form.get('garmin_record_seconds', '600'))
            save_setting(DB_KEY_GARMIN_MAX_RECORDING_DURATION, request.form.get('garmin_max_recording_duration', '3600'))
            save_setting(DB_KEY_GARMIN_STT_OUTPUT_ENABLED, request.form.get('garmin_stt_output_enabled', 'true'))

            # Handle General Settings
            save_setting(DB_KEY_LOG_LEVEL, request.form.get('log_level', 'INFO'))
            save_setting(DB_KEY_DISCORD_LOG_LEVEL, request.form.get('discord_log_level', 'INFO'))
            logger.info(f"Saved DISCORD_LOG_LEVEL: {request.form.get('discord_log_level', 'INFO')}")

            # Handle Cleanup Settings
            cleanup_aligned_hours = request.form.get('cleanup_aligned_recordings_hours', '48')
            save_setting(DB_KEY_CLEANUP_ALIGNED_RECORDINGS_HOURS, cleanup_aligned_hours)
            logger.info(f"Saved {DB_KEY_CLEANUP_ALIGNED_RECORDINGS_HOURS}: {cleanup_aligned_hours}")

            cleanup_garmin_hours = request.form.get('cleanup_garmin_output_hours', '72')
            save_setting(DB_KEY_CLEANUP_GARMIN_OUTPUT_HOURS, cleanup_garmin_hours)
            logger.info(f"Saved {DB_KEY_CLEANUP_GARMIN_OUTPUT_HOURS}: {cleanup_garmin_hours}")

            # Reload settings into global scope
            cfg.load_all_settings()
            
            # Apply Discord log level immediately after reloading settings
            cfg.apply_discord_log_level()
            
            if cfg.TESTING:
                logger.info(f"Settings Route - TESTING MODE ACTIVE: Overriding LOG_CHANNEL_ID and BOT_AUDIT_ID to {TESTING_CHANNEL_ID}.")
                cfg.LOG_CHANNEL_ID = TESTING_CHANNEL_ID
                cfg.BOT_AUDIT_ID = TESTING_CHANNEL_ID
            else:
                logger.info(f"Settings Route - TESTING MODE INACTIVE. LOG_CHANNEL_ID: {cfg.LOG_CHANNEL_ID}, BOT_AUDIT_ID: {cfg.BOT_AUDIT_ID}.")

            message = "Settings saved successfully. Note: Some changes may require a bot restart to take full effect."

        except Exception as e:
            logger.error(f"Error saving settings: {e}", exc_info=True)
            error = f"Error saving settings: {e}"

    # Scan for available Vosk models
    vosk_models = []
    try:
        import os
        current_dir = os.getcwd()
        logger.debug(f"Flask server working directory: {current_dir}")
        
        # Try multiple approaches to find the models directory
        possible_paths = [
            Path(os.path.join(SCRIPT_DIR, "data", "assets", "models")),
            Path("data/assets/models"),
            Path(os.path.join(os.getcwd(), "data", "assets", "models"))
        ]
        
        vosk_model_dir = None
        for path in possible_paths:
            logger.debug(f"Trying path: {path.absolute()}")
            if path.exists() and path.is_dir():
                vosk_model_dir = path
                logger.debug(f"Found vosk-model directory: {vosk_model_dir.absolute()}")
                break
        
        if not vosk_model_dir:
            logger.warning("data/assets/models directory not found in any of the expected locations")
            logger.debug(f"SCRIPT_DIR: {SCRIPT_DIR}")
            logger.debug(f"Current working directory: {os.getcwd()}")
            logger.debug(f"Tried paths: {[str(p.absolute()) for p in possible_paths]}")
            return render_template('settings.html', current_settings=current_settings_display, message=message, error=error, vosk_models=[])
        
        if vosk_model_dir.exists() and vosk_model_dir.is_dir():
            # Check if there are subdirectories (like vosk-model-de-0.21, vosk-model-en, etc.)
            subdirs = [item for item in vosk_model_dir.iterdir() if item.is_dir()]
            logger.debug(f"Found subdirectories: {[str(item) for item in subdirs]}")
            
            if subdirs:
                # If there are subdirectories, use them
                for item in subdirs:
                    model_path = str(item) + "/"
                    vosk_models.append(model_path)
                    logger.debug(f"Added model path: {model_path}")
            else:
                # If no subdirectories, check if this is a direct model directory
                # Look for typical Vosk model files
                model_files = list(vosk_model_dir.glob("*.conf")) + list(vosk_model_dir.glob("am"))
                logger.debug(f"Found model files: {[str(f) for f in model_files]}")
                if model_files:
                    # This appears to be a direct model directory
                    model_path = str(vosk_model_dir) + "/"
                    vosk_models.append(model_path)
                    logger.debug(f"Added direct model path: {model_path}")
            
            vosk_models.sort()  # Sort alphabetically
            logger.debug(f"Final sorted list: {vosk_models}")
            logger.info(f"Found {len(vosk_models)} Vosk models: {vosk_models}")
        else:
            logger.warning("data/assets/models directory not found")
    except Exception as e:
        logger.error(f"Error scanning data/assets/models directory: {e}", exc_info=True)

    current_settings_display = {}
    
    token_env = os.environ.get('DISCORD_TOKEN', '')
    if token_env and len(token_env) > 8:
        current_settings_display['DISCORD_TOKEN_DISPLAY'] = f"{token_env[:4]}...{token_env[-4:]}"
    else:
        current_settings_display['DISCORD_TOKEN_DISPLAY'] = "Token not set in environment"

    current_settings_display['APP_TESTING_MODE'] = str(cfg.TESTING).lower()
    current_settings_display['HIDDEN_CHANNELS'] = ','.join(map(str, cfg.HIDDEN_CHANNELS)) if cfg.HIDDEN_CHANNELS else ''
    current_settings_display['LOG_CHANNEL_ID'] = str(cfg.LOG_CHANNEL_ID) if cfg.LOG_CHANNEL_ID is not None else ''
    current_settings_display['BOT_AUDIT_ID'] = str(cfg.BOT_AUDIT_ID) if cfg.BOT_AUDIT_ID is not None else ''
    current_settings_display['TECHSUPPORT_CHANNEL_ID'] = str(cfg.TECHSUPPORT_CHANNEL_ID) if cfg.TECHSUPPORT_CHANNEL_ID is not None else ''
    current_settings_display['AFK_CHANNEL_ID'] = str(cfg.AFK_CHANNEL_ID) if cfg.AFK_CHANNEL_ID is not None else ''
    current_settings_display['PURGE_OLDER_THAN_DAYS'] = str(cfg.PURGE_OLDER_THAN_DAYS)
    current_settings_display['JOIN_MESSAGE_TIMER_ENABLED'] = str(cfg.JOIN_MESSAGE_TIMER_ENABLED).lower()
    current_settings_display['JOIN_MESSAGE_TIMER_MINUTES'] = str(cfg.JOIN_MESSAGE_TIMER_MINUTES)
    current_settings_display['AFK_TIMER_MINUTES'] = str(cfg.AFK_TIMER_MINUTES)
    
    # Garmin Recorder Settings
    current_settings_display['STT_ENABLED'] = str(cfg.STT_ENABLED).lower()
    current_settings_display['STT_ENGINE'] = cfg.STT_ENGINE
    current_settings_display['VOSK_MODEL_PATH'] = cfg.VOSK_MODEL_PATH
    current_settings_display['GARMIN_AUTO_JOIN_ENABLED'] = str(cfg.GARMIN_AUTO_JOIN_ENABLED).lower()
    current_settings_display['GARMIN_AUTO_JOIN_CHANNELS'] = ','.join(map(str, cfg.GARMIN_AUTO_JOIN_CHANNELS)) if cfg.GARMIN_AUTO_JOIN_CHANNELS else ''
    current_settings_display['GARMIN_RECORD_SECONDS'] = str(cfg.GARMIN_RECORD_SECONDS)
    current_settings_display['GARMIN_MAX_RECORDING_DURATION'] = str(cfg.GARMIN_MAX_RECORDING_DURATION)
    current_settings_display['GARMIN_STT_OUTPUT_ENABLED'] = str(cfg.GARMIN_STT_OUTPUT_ENABLED).lower()

    # General Settings
    current_settings_display['LOG_LEVEL'] = cfg.LOG_LEVEL
    current_settings_display['DISCORD_LOG_LEVEL'] = cfg.DISCORD_LOG_LEVEL

    # Cleanup Settings
    current_settings_display['CLEANUP_ALIGNED_RECORDINGS_HOURS'] = str(cfg.CLEANUP_ALIGNED_RECORDINGS_HOURS)
    current_settings_display['CLEANUP_GARMIN_OUTPUT_HOURS'] = str(cfg.CLEANUP_GARMIN_OUTPUT_HOURS)

    return render_template('settings.html', current_settings=current_settings_display, message=message, error=error, vosk_models=vosk_models)

@app.route('/restart_bot', methods=['POST'])
async def restart_bot_route():
    if request.method == 'POST':
        logger.info("Restart command received via web UI.")
        if bot.loop:
            logger.info("Scheduling graceful_shutdown via bot's event loop.")
            asyncio.run_coroutine_threadsafe(graceful_shutdown(), bot.loop)
            # Optionally, add a message to be displayed on the settings page after redirect
            # For example, using Flask's flash messaging:
            # flash("Bot shutdown initiated. It should restart if a process manager is active.", "info")
        else:
            logger.error("Bot event loop not available. Cannot schedule graceful_shutdown.")
            # Optionally, flash an error message:
            # flash("Error: Bot event loop not available. Cannot initiate restart.", "error")
        
        # Redirect back to the settings page (or home)
        # The actual shutdown happens in the background.
        return redirect(url_for('settings_route'))

# Garmin Control API Routes
def get_status_data():
    """Get dynamic status data for the home page"""
    try:
        # Get voice events count
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_voice_events")
        voice_events_count = cursor.fetchone()[0]
        conn.close()
        
        # Get recordings count
        import os
        from pathlib import Path
        output_dir = Path(GARMIN_OUTPUT_DIR)
        recordings_count = 0
        if output_dir.exists():
            recordings_count = len(list(output_dir.glob("*.mp3")))
        
        # Get online users count (approximate - users in voice channels)
        online_users_count = 0
        try:
            if bot.is_ready():
                for guild in bot.guilds:
                    for channel in guild.voice_channels:
                        online_users_count += len(channel.members)
        except:
            online_users_count = 0
        
        # Get Garmin health data
        garmin_health = get_garmin_health_data()
        
        # Get cleanup statistics
        cleanup_stats = get_cleanup_stats()
        
        return {
            'voice_events': voice_events_count,
            'recordings': recordings_count,
            'online_users': online_users_count,
            'garmin_health': garmin_health,
            'cleanup_stats': cleanup_stats
        }
    except Exception as e:
        logger.error(f"Error getting status data: {e}")
        return {
            'voice_events': 0,
            'recordings': 0,
            'online_users': 0,
            'garmin_health': {
                'autojoin': False,
                'recording': False,
                'duration': '0.0s',
                'buffer': '0.00 MB',
                'errors': '0/5',  # Default fallback - will be updated by get_garmin_health_data() if available
                'processing': 'Idle'
            },
            'cleanup_stats': {
                'aligned_recordings': {'exists': False, 'file_count': 0, 'total_size_mb': 0},
                'garmin_output': {'exists': False, 'file_count': 0, 'total_size_mb': 0}
            }
        }

def get_garmin_health_data():
    """Get Garmin health data for status display"""
    try:
        if garmin_manager:
            health = garmin_manager.get_recording_health()
            is_connected = health.get('connected', False)
            
            # Only show duration if actually connected and recording
            if is_connected:
                duration = f"{health.get('recording_duration', 0):.1f}s"
            else:
                duration = '0.0s'
            
            # Get actual error values from the health data
            recording_errors = health.get('recording_errors', 0)
            max_errors = health.get('max_errors', 5)
            
            return {
                'autojoin': cfg.GARMIN_AUTO_JOIN_ENABLED,  # Get from config
                'recording': is_connected,
                'duration': duration,
                'buffer': f"{health.get('buffer_size', 0) / (1024*1024):.2f} MB",
                'errors': f"{recording_errors}/{max_errors}",
                'processing': 'Processing' if health.get('is_processing', False) else 'Idle',
                'stt_output': cfg.GARMIN_STT_OUTPUT_ENABLED  # Get from config
            }
        else:
            # Get max_errors from environment or use default
            import os
            max_errors = int(os.getenv("GARMIN_MAX_RECORDING_ERRORS", "5"))
            return {
                'autojoin': cfg.GARMIN_AUTO_JOIN_ENABLED,  # Get from config
                'recording': False,
                'duration': '0.0s',
                'buffer': '0.00 MB',
                'errors': f"0/{max_errors}",
                'processing': 'Idle',
                'stt_output': cfg.GARMIN_STT_OUTPUT_ENABLED  # Get from config
            }
    except Exception as e:
        logger.error(f"Error getting Garmin health data: {e}")
        # Get max_errors from environment or use default
        import os
        max_errors = int(os.getenv("GARMIN_MAX_RECORDING_ERRORS", "5"))
        return {
            'autojoin': cfg.GARMIN_AUTO_JOIN_ENABLED,  # Get from config
            'recording': False,
            'duration': '0.0s',
            'buffer': '0.00 MB',
            'errors': f"0/{max_errors}",
            'processing': 'Idle',
            'stt_output': cfg.GARMIN_STT_OUTPUT_ENABLED  # Get from config
        }

@app.route('/garmin/start', methods=['POST'])
def garmin_start_route():
    try:
        # Use the global garmin_manager
        if garmin_manager:
            # Check if already connected
            if garmin_manager.is_connected():
                return {"success": True, "message": "Already connected to a voice channel"}
            
            # Try to find a voice channel to join
            try:
                if bot.is_ready() and bot.loop and bot.loop.is_running():
                    for guild in bot.guilds:
                        for channel in guild.voice_channels:
                            if len(channel.members) > 0:  # Join a channel with users
                                # Schedule the coroutine in the bot's event loop
                                future = asyncio.run_coroutine_threadsafe(garmin_manager.join_channel(channel), bot.loop)
                                try:
                                    future.result(timeout=10)  # Wait up to 10 seconds
                                    return {"success": True, "message": f"Garmin recording started in {channel.name}"}
                                except Exception as e:
                                    return {"success": False, "error": f"Failed to join channel {channel.name}: {str(e)}"}
                    
                    # If no channels with users, try the first available channel
                    for guild in bot.guilds:
                        for channel in guild.voice_channels:
                            # Schedule the coroutine in the bot's event loop
                            future = asyncio.run_coroutine_threadsafe(garmin_manager.join_channel(channel), bot.loop)
                            try:
                                future.result(timeout=10)  # Wait up to 10 seconds
                                return {"success": True, "message": f"Garmin recording started in {channel.name}"}
                            except Exception as e:
                                return {"success": False, "error": f"Failed to join channel {channel.name}: {str(e)}"}
                
                return {"success": False, "error": "No voice channels available to join"}
            except Exception as e:
                return {"success": False, "error": f"Failed to join voice channel: {str(e)}"}
        else:
            return {"success": False, "error": "Garmin manager not available"}
    except Exception as e:
        logger.error(f"Error in garmin_start_route: {e}")
        return {"success": False, "error": str(e)}

@app.route('/garmin/stop', methods=['POST'])
def garmin_stop_route():
    try:
        # Use the global garmin_manager
        if garmin_manager:
            # Use the bot's event loop instead of creating a new one
            if bot.loop and bot.loop.is_running():
                # Schedule the coroutine in the bot's event loop
                future = asyncio.run_coroutine_threadsafe(garmin_manager.leave_channel(), bot.loop)
                try:
                    future.result(timeout=10)  # Wait up to 10 seconds
                    return {"success": True, "message": "Garmin recording stopped successfully"}
                except Exception as e:
                    return {"success": False, "error": f"Failed to stop recording: {str(e)}"}
            else:
                return {"success": False, "error": "Bot event loop not available"}
        else:
            return {"success": False, "error": "Garmin manager not available"}
    except Exception as e:
        logger.error(f"Error in garmin_stop_route: {e}")
        return {"success": False, "error": str(e)}

@app.route('/garmin/save', methods=['POST'])
def garmin_save_route():
    try:
        # Use the global garmin_manager
        if garmin_manager:
            # Call the garmin manager directly
            health_data = garmin_manager.get_recording_health()
            
            if health_data["connected"] and health_data["buffer_size"] > 0:
                garmin_manager.save_recording()
                return {"success": True, "message": "Garmin recording saved successfully"}
            else:
                if not health_data["connected"]:
                    return {"success": False, "error": "Not connected to any voice channel"}
                else:
                    return {"success": False, "error": "No audio data to save"}
        else:
            return {"success": False, "error": "Garmin manager not available"}
    except Exception as e:
        logger.error(f"Error in garmin_save_route: {e}")
        return {"success": False, "error": str(e)}

@app.route('/garmin/autojoin', methods=['POST'])
def garmin_autojoin_route():
    try:
        # Toggle the autojoin setting
        current_setting = cfg.GARMIN_AUTO_JOIN_ENABLED
        new_setting = not current_setting
        
        # Save the new setting to the database
        from config_loader import save_setting, DB_KEY_GARMIN_AUTO_JOIN_ENABLED
        save_setting(DB_KEY_GARMIN_AUTO_JOIN_ENABLED, str(new_setting).lower())
        
        # Reload settings
        cfg.load_all_settings()
        
        status = "enabled" if new_setting else "disabled"
        return {"success": True, "message": f"Auto-join {status} successfully"}
    except Exception as e:
        logger.error(f"Error in garmin_autojoin_route: {e}")
        return {"success": False, "error": str(e)}

@app.route('/garmin/stt_output', methods=['POST'])
def garmin_stt_output_route():
    try:
        # Toggle the STT output setting
        current_setting = cfg.GARMIN_STT_OUTPUT_ENABLED
        new_setting = not current_setting
        
        # Save the new setting to the database
        from config_loader import save_setting, DB_KEY_GARMIN_STT_OUTPUT_ENABLED
        save_setting(DB_KEY_GARMIN_STT_OUTPUT_ENABLED, str(new_setting).lower())
        
        # Reload settings
        cfg.load_all_settings()
        
        status = "enabled" if new_setting else "disabled"
        return {"success": True, "message": f"STT output {status} successfully"}
    except Exception as e:
        logger.error(f"Error in garmin_stt_output_route: {e}")
        return {"success": False, "error": str(e)}

@app.route('/status')
def status_route():
    """API endpoint to get current status data for auto-refresh"""
    try:
        status_data = get_status_data()
        return {"success": True, "status": status_data}
    except Exception as e:
        logger.error(f"Error in status_route: {e}")
        return {"success": False, "error": str(e)}

@app.route('/cleanup/run', methods=['POST'])
def cleanup_run_route():
    """API endpoint to manually trigger audio cleanup"""
    try:
        logger.info("Manual cleanup triggered via web UI")
        aligned_deleted, garmin_deleted = run_cleanup()
        
        return {
            "success": True,
            "aligned_deleted": aligned_deleted,
            "garmin_deleted": garmin_deleted,
            "message": f"Cleanup completed: {aligned_deleted + garmin_deleted} files deleted"
        }
    except Exception as e:
        logger.error(f"Error in cleanup_run_route: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

@app.route('/cleanup/stats')
def cleanup_stats_route():
    """API endpoint to get cleanup statistics"""
    try:
        stats = get_cleanup_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"Error in cleanup_stats_route: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

def run_flask():
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8080))
    # Diese print-Anweisung ist eine einmalige Startmeldung für Waitress und kann so bleiben.
    # print(f"Starte Waitress WSGI-Server auf {host}:{port}") # Original print replaced by logger
    logger.info(f"Attempting to start Flask server (Waitress) on {host}:{port}. If you see an 'Address already in use' error, try setting the PORT environment variable to a different value.")
    serve(app, host=host, port=port, threads=4)

# --------- Discord-Bot Setup ---------
intents = Intents.default()
intents.guilds = True
intents.message_content = True
intents.messages = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!!", intents=intents)

# Konfiguration
DISCORD_SERVER_ID = 374159356717039616
TECHSUPPORT_CHANNEL_ID = 1139952610883928134
CLOSED_TAG_NAME = "🔒 CLOSED"

# Testing Mode Configuration
TESTING_CHANNEL_ID = 1376227809474908253 # User-provided ID for testing channel

# Global variables to be populated by config_loader
TESTING = False
LOG_CHANNEL_ID = 0
BOT_AUDIT_ID = 0
HIDDEN_CHANNELS = []
USERS: List[str] = []
IMAGES_FOLDER = os.path.join(SCRIPT_DIR, "data", "assets", "images")
GARMIN_AUTO_JOIN_ENABLED = False
GARMIN_AUTO_JOIN_CHANNELS = []

shutdown_initiated = False
garmin_manager = None

# --------- User Log Database Initialization Function ---------
def init_user_log_db():
    # No need to create config directory anymore since we moved to data/
    is_test_db = DATABASE_PATH == ':memory:'
    if not is_test_db:
        logger.info(f"Attempting to initialize database at: {DATABASE_PATH}")

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        if not is_test_db:
            logger.info(f"Successfully connected to database: {DATABASE_PATH}")
    except sqlite3.Error as e:
        logger.error(f"SQLite error during connect to {DATABASE_PATH}: {e}", exc_info=True)
        if conn:
            conn.close()
        return
    except Exception as e:
        logger.error(f"Unexpected error during connect to {DATABASE_PATH}: {e}", exc_info=True)
        if conn:
            conn.close()
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_voice_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                channel_id INTEGER,
                channel_name TEXT,
                event_type TEXT, -- 'join' or 'leave'
                timestamp TEXT
            )
        """)
        conn.commit()

        # Drop the old user_joins table if it exists
        cursor.execute("DROP TABLE IF EXISTS user_joins")
        conn.commit()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inactive_threads (
                thread_id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                last_activity_timestamp TEXT,
                warning_sent_timestamp TEXT,
                reminder_sent_timestamp TEXT,
                op_user_id INTEGER,
                last_message_user_id INTEGER
            )
        """)
        conn.commit()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_name TEXT PRIMARY KEY,
                setting_value TEXT
            )
        """)
        conn.commit()

        if not is_test_db:
            logger.info("All database tables ensured to exist.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error during table creation: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error during table creation: {e}", exc_info=True)
    finally:
        if conn and not is_test_db:
            conn.close()
            logger.info(f"Database connection to {DATABASE_PATH} closed after init.")

# --------- Helper Functions for User Voice Events ---------
def log_voice_event(user_id: int, username: str, channel_id: int, channel_name: str, event_type: str):
    """Logs a user join or leave event to the database."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO user_voice_events (user_id, username, channel_id, channel_name, event_type, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, channel_id, channel_name, event_type, timestamp))
        conn.commit()
        logger.info(f"Logged voice event: User {username} ({user_id}) {event_type} channel {channel_name} ({channel_id})")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in log_voice_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in log_voice_event: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --------- Helper Functions for inactive_threads Table ---------
def add_or_update_thread_activity(thread_id: int, guild_id: int, last_activity_timestamp_iso: str, op_user_id: int, last_message_user_id: int):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Try to insert, if it fails (because thread_id exists), then update
        try:
            cursor.execute("""
                INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id, warning_sent_timestamp, reminder_sent_timestamp)
                VALUES (?, ?, ?, ?, ?, NULL, NULL)
            """, (thread_id, guild_id, last_activity_timestamp_iso, op_user_id, last_message_user_id))
            logger.info(f"New activity recorded for thread {thread_id}: Inserted into inactive_threads.")
        except sqlite3.IntegrityError: # This means thread_id already exists
            cursor.execute("""
                UPDATE inactive_threads
                SET last_activity_timestamp = ?,
                    op_user_id = ?,
                    last_message_user_id = ?,
                    warning_sent_timestamp = NULL,
                    reminder_sent_timestamp = NULL
                WHERE thread_id = ?
            """, (last_activity_timestamp_iso, op_user_id, last_message_user_id, thread_id))
            logger.info(f"Activity updated for thread {thread_id}: Updated existing record in inactive_threads, reset warning/reminder.")
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"SQLite error in add_or_update_thread_activity for thread {thread_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in add_or_update_thread_activity for thread {thread_id}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

def get_thread_activity(thread_id: int) -> Optional[sqlite3.Row]:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row # To access columns by name
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        record = cursor.fetchone()
        if record:
            logger.debug(f"Thread activity record found for thread {thread_id}.")
            return record
        else:
            logger.debug(f"No thread activity record found for thread {thread_id}.")
            return None
    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_thread_activity for thread {thread_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"General error in get_thread_activity for thread {thread_id}: {e}", exc_info=True)
        return None
    finally:
        if conn:
            conn.close()

async def scan_existing_threads():
    logger.info("Starting scan of existing tech support threads...")
    if not TECHSUPPORT_CHANNEL_ID:
        logger.error("TECHSUPPORT_CHANNEL_ID is not configured. Cannot scan existing threads.")
        return

    try:
        tech_forum_channel = bot.get_channel(TECHSUPPORT_CHANNEL_ID)
        if not tech_forum_channel:
            try:
                tech_forum_channel = await bot.fetch_channel(TECHSUPPORT_CHANNEL_ID)
            except discord.NotFound:
                logger.error(f"Tech support forum channel (ID: {TECHSUPPORT_CHANNEL_ID}) not found.")
                return
            except discord.Forbidden:
                logger.error(f"Forbidden to fetch tech support forum channel (ID: {TECHSUPPORT_CHANNEL_ID}).")
                return
            except Exception as e:
                logger.error(f"Error fetching tech support forum channel (ID: {TECHSUPPORT_CHANNEL_ID}): {e}", exc_info=True)
                return
        
        if not isinstance(tech_forum_channel, discord.ForumChannel):
            logger.error(f"Channel with ID {TECHSUPPORT_CHANNEL_ID} is not a ForumChannel. Cannot scan threads.")
            return

        logger.info(f"Successfully fetched tech support forum: '{tech_forum_channel.name}' (ID: {tech_forum_channel.id})")
        closed_tag = await get_forum_tag_by_name(tech_forum_channel, CLOSED_TAG_NAME)
        if not closed_tag:
            logger.warning(f"'{CLOSED_TAG_NAME}' tag not found in forum '{tech_forum_channel.name}'. Will process all threads as if not closed by tag.")

        processed_threads = 0
        updated_threads = 0
        threads_to_scan = tech_forum_channel.threads # Get a list of threads once
        # Also include archived threads if possible and relevant, though this example focuses on active ones.
        # If you need to scan archived threads:
        # archived_threads = await tech_forum_channel.archived_threads(limit=None).flatten()
        # threads_to_scan.extend(archived_threads) # Be mindful of duplicates if any thread can be in both lists

        logger.info(f"Found {len(threads_to_scan)} threads in '{tech_forum_channel.name}'. Iterating now...")

        for thread in threads_to_scan:
            logger.debug(f"Scanning thread: '{thread.name}' (ID: {thread.id})")
            if closed_tag and closed_tag in thread.applied_tags:
                logger.info(f"Thread '{thread.name}' (ID: {thread.id}) is closed (has '{CLOSED_TAG_NAME}' tag). Skipping.")
                continue

            if thread.archived or thread.locked: # Also skip if manually archived/locked by other means
                 logger.info(f"Thread '{thread.name}' (ID: {thread.id}) is archived or locked. Skipping.")
                 continue

            last_message = None
            try:
                # Attempt to fetch the last message
                messages = [msg async for msg in thread.history(limit=1)] # Default is newest first
                if not messages:
                    logger.info(f"Thread '{thread.name}' (ID: {thread.id}) is empty or history is inaccessible. Skipping.")
                    continue
                last_message = messages[0]
                logger.debug(f"Last message in thread '{thread.name}' by {last_message.author.name} at {last_message.created_at}")
            except discord.Forbidden:
                logger.warning(f"Forbidden to fetch history for thread '{thread.name}' (ID: {thread.id}). Skipping.")
                continue
            except Exception as e:
                logger.error(f"Error fetching history for thread '{thread.name}' (ID: {thread.id}): {e}", exc_info=True)
                continue
            
            op_user_id = thread.owner_id
            if not op_user_id: # owner_id can be None if the user who created the thread (post) left the server
                logger.info(f"owner_id is None for thread '{thread.name}' (ID: {thread.id}). Attempting to fetch starter message.")
                try:
                    # The thread ID itself is the ID of the starter message in forum post threads
                    starter_message = await thread.fetch_message(thread.id)
                    if starter_message:
                        op_user_id = starter_message.author.id
                        logger.info(f"OP user ID for thread '{thread.name}' (ID: {thread.id}) from fetched starter message: {op_user_id}")
                    else:
                        # This case should ideally not happen if fetch_message(thread.id) works for forum posts
                        logger.warning(f"Could not fetch starter message for thread '{thread.name}' (ID: {thread.id}) to determine OP. Skipping.")
                        continue
                except discord.NotFound:
                    logger.warning(f"Starter message for thread '{thread.name}' (ID: {thread.id}) not found (thread ID might not be the starter message ID if it's not a forum post, or post deleted). Skipping.")
                    continue
                except discord.Forbidden:
                    logger.warning(f"Forbidden to fetch starter message for thread '{thread.name}' (ID: {thread.id}). Skipping.")
                    continue
                except Exception as e:
                    logger.error(f"Error fetching starter message for thread '{thread.name}' (ID: {thread.id}): {e}", exc_info=True)
                    continue
            
            if not op_user_id: # Should be redundant now, but as a safeguard
                logger.error(f"Failed to determine OP user ID for thread '{thread.name}' (ID: {thread.id}) after all attempts. Skipping.")
                continue

            last_message_author_id = last_message.author.id
            last_activity_ts_iso = last_message.created_at.isoformat()

            logger.info(f"Updating activity for thread '{thread.name}' (ID: {thread.id}). OP: {op_user_id}, Last Poster: {last_message_author_id}, Last Activity: {last_activity_ts_iso}")
            add_or_update_thread_activity(
                thread_id=thread.id,
                guild_id=thread.guild.id,
                last_activity_timestamp_iso=last_activity_ts_iso,
                op_user_id=op_user_id,
                last_message_user_id=last_message_author_id
            )
            updated_threads += 1
            processed_threads +=1 # Count as processed if we attempted an update

        logger.info(f"Scan of existing tech support threads completed. Processed: {processed_threads}, Updated/Added: {updated_threads} threads.")

    except Exception as e:
        logger.error(f"An unexpected error occurred during scan_existing_threads: {e}", exc_info=True)

# --- Additional DB Helper Functions for inactive_threads ---
def get_all_thread_activities() -> List[sqlite3.Row]:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inactive_threads")
        records = cursor.fetchall()
        logger.debug(f"Fetched {len(records)} thread activity records from DB.")
        return records
    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_all_thread_activities: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"General error in get_all_thread_activities: {e}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()

def remove_thread_activity(thread_id: int):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Removed thread activity record for thread_id: {thread_id}")
        else:
            logger.warning(f"Attempted to remove thread activity for thread_id: {thread_id}, but no record was found.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in remove_thread_activity for thread {thread_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in remove_thread_activity for thread {thread_id}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

def update_thread_warning_sent(thread_id: int, timestamp_iso: str):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE inactive_threads SET warning_sent_timestamp = ? WHERE thread_id = ?", (timestamp_iso, thread_id))
        conn.commit()
        logger.info(f"Updated warning_sent_timestamp for thread_id: {thread_id} to {timestamp_iso}")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in update_thread_warning_sent for thread {thread_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in update_thread_warning_sent for thread {thread_id}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

def update_thread_reminder_sent(thread_id: int, timestamp_iso: str):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE inactive_threads SET reminder_sent_timestamp = ? WHERE thread_id = ?", (timestamp_iso, thread_id))
        conn.commit()
        logger.info(f"Updated reminder_sent_timestamp for thread_id: {thread_id} to {timestamp_iso}")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in update_thread_reminder_sent for thread {thread_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in update_thread_reminder_sent for thread {thread_id}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --------- Helper Functions for bot_settings Table ---------
import config_loader as cfg
from config_loader import save_setting, DB_KEY_APP_TESTING_MODE, DB_KEY_HIDDEN_CHANNELS, DB_KEY_LOG_CHANNEL_ID, DB_KEY_BOT_AUDIT_ID, DB_KEY_TECHSUPPORT_CHANNEL_ID, DB_KEY_AFK_CHANNEL_ID, DB_KEY_PURGE_OLDER_THAN_DAYS, DB_KEY_JOIN_MESSAGE_TIMER_ENABLED, DB_KEY_JOIN_MESSAGE_TIMER_MINUTES, DB_KEY_AFK_TIMER_MINUTES, DB_KEY_STT_ENABLED, DB_KEY_STT_ENGINE, DB_KEY_VOSK_MODEL_PATH, DB_KEY_GARMIN_AUTO_JOIN_ENABLED, DB_KEY_GARMIN_AUTO_JOIN_CHANNELS, DB_KEY_GARMIN_RECORD_SECONDS, DB_KEY_GARMIN_MAX_RECORDING_DURATION, DB_KEY_GARMIN_STT_OUTPUT_ENABLED, DB_KEY_LOG_LEVEL, DB_KEY_DISCORD_LOG_LEVEL, DB_KEY_CLEANUP_ALIGNED_RECORDINGS_HOURS, DB_KEY_CLEANUP_GARMIN_OUTPUT_HOURS



async def send_log_message(msg: str, embed: Optional[Embed] = None, target_channel_ids: Optional[List[int]] = None):
    if target_channel_ids is None:
        if cfg.BOT_AUDIT_ID:
            target_channel_ids = [cfg.BOT_AUDIT_ID]
        else:
            logger.error(f"send_log_message: BOT_AUDIT_ID ist nicht konfiguriert. Nachricht kann nicht gesendet werden: {msg}")
            return

    if not target_channel_ids:
        logger.warning(f"send_log_message: Keine Zielkanäle für Nachricht: {msg}")
        return

    for channel_id in target_channel_ids:
        if channel_id is None:
            logger.warning(f"send_log_message: Ungültige Kanal-ID (None) beim Senden der Log-Nachricht übersprungen: {msg}")
            continue
        try:
            if not bot.is_ready() and not (bot.loop and bot.loop.is_running()):
                logger.warning(f"send_log_message: Log-Nachricht an {channel_id} kann nicht gesendet werden, Bot nicht bereit/Loop nicht vorhanden: {msg}")
                continue

            log_channel_obj = bot.get_channel(channel_id)
            if not log_channel_obj:
                if bot.user and bot.loop and bot.loop.is_running() and not bot.is_closed():
                    try:
                        log_channel_obj = await bot.fetch_channel(channel_id)
                    except discord.NotFound:
                        logger.warning(f"send_log_message: Log-Kanal {channel_id} nicht gefunden (fetch_channel). Nachricht: {msg}")
                    except discord.Forbidden:
                        logger.warning(f"send_log_message: Log-Kanal {channel_id} nicht zugänglich (fetch_channel, Forbidden). Nachricht: {msg}")
                    except Exception as e_fetch:
                        logger.error(f"send_log_message: Fehler beim Holen des Log-Kanals {channel_id}: {e_fetch}. Nachricht: {msg}", exc_info=True)
                else:
                    logger.warning(f"send_log_message: Log-Kanal {channel_id} nicht gefunden (get_channel) und Bot nicht bereit für fetch. Nachricht: {msg}")

            if log_channel_obj:
                await log_channel_obj.send(msg, embed=embed)
            else:
                logger.error(f"send_log_message: Log-Kanal {channel_id} konnte endgültig nicht gefunden werden. Nachricht: {msg}")
        except Exception as e:
            logger.error(f"send_log_message: Fehler beim Senden der Log-Nachricht an Kanal {channel_id}: {e}. Ursprüngliche Nachricht: {msg}", exc_info=True)


async def get_forum_tag_by_name(forum_channel: discord.ForumChannel, tag_name: str) -> discord.ForumTag | None:
    if not forum_channel:
        return None
    for tag in forum_channel.available_tags:
        if tag.name.lower() == tag_name.lower():
            return tag
    return None

async def close_support_thread(thread: Thread, trigger_source: str, set_tag: bool = True):
    try:
        actions_performed = []
        log_actions = []

        if not thread.locked:
            await thread.edit(locked=True)
            actions_performed.append("gesperrt")
            log_actions.append("gesperrt")

        closed_tag_object = None
        if set_tag and isinstance(thread.parent, discord.ForumChannel):
            closed_tag_object = await get_forum_tag_by_name(thread.parent, CLOSED_TAG_NAME)
            if closed_tag_object:
                current_tags = thread.applied_tags
                if closed_tag_object not in current_tags:
                    new_tags = [t for t in current_tags if t.name.lower() != CLOSED_TAG_NAME.lower()]
                    new_tags.append(closed_tag_object)
                    try:
                        await thread.edit(applied_tags=new_tags[:5]) 
                        actions_performed.append(f"Tag '{CLOSED_TAG_NAME}' gesetzt")
                        log_actions.append("Tag gesetzt")
                    except discord.HTTPException as e:
                        logger.error(f"Fehler beim Setzen des Tags für Thread '{thread.name}': {e}", exc_info=True)
                        await send_log_message(f"⚠️ Fehler beim Setzen des Tags '{CLOSED_TAG_NAME}' für Thread '{thread.name}': {e.text}", target_channel_ids=[cfg.BOT_AUDIT_ID])
            else:
                logger.warning(f"Tag '{CLOSED_TAG_NAME}' wurde im Forum '{thread.parent.name}' nicht gefunden.")
                await send_log_message(f"⚠️ Warnung: Tag '{CLOSED_TAG_NAME}' im Forum '{thread.parent.name}' nicht gefunden für Thread '{thread.name}'.", target_channel_ids=[cfg.BOT_AUDIT_ID])

        if actions_performed or not thread.archived:
            message_parts = ["🔒 Dieser Support-Thread wurde"]
            if trigger_source:
                message_parts.append(f"durch {trigger_source}")
            if actions_performed:
                message_parts.append(f"{' und '.join(actions_performed)}")
            else:
                message_parts.append("bearbeitet")
            message_parts.append("und wird nun archiviert.")
            await thread.send(" ".join(message_parts))

        if not thread.archived:
            await thread.edit(archived=True)
            log_actions.append("archiviert")

        if log_actions:
            action_str = " und ".join(log_actions)
            logger.info(f"Thread '{thread.name}' wurde durch {trigger_source} {action_str}.")
            await send_log_message(f"🧵 Thread '{thread.name}' (ID: {thread.id}) durch '{trigger_source}' {action_str}.", target_channel_ids=[cfg.BOT_AUDIT_ID])

    except discord.Forbidden:
        err_msg = f"Fehler: Keine Berechtigung, den Thread '{thread.name}' zu bearbeiten (sperren/Tag/archivieren)."
        logger.error(err_msg)
        await send_log_message(f"⚠️ {err_msg}", target_channel_ids=[cfg.BOT_AUDIT_ID])
        try:
            await thread.send(f"Fehler: Ich habe nicht die nötigen Berechtigungen, um diesen Thread zu sperren, den Tag zu setzen oder zu archivieren. Bitte überprüfe meine Rollen und Berechtigungen im Kanal '{thread.parent.name}'.")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Generischer Fehler beim Schließen des Threads '{thread.name}': {e}", exc_info=True)
        await send_log_message(f"⚠️ Fehler beim Schließen des Threads '{thread.name}' (ID: {thread.id}): {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])

@bot.event
async def on_ready():
    logger.info(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    logger.info(f"Bot version: {BOT_VERSION} starting up...")
    
    if not os.path.exists(IMAGES_FOLDER):
        os.makedirs(IMAGES_FOLDER)
        logger.info(f"Ordner '{IMAGES_FOLDER}' wurde erstellt. Bitte füge Bilder hinzu.")
        await send_log_message(f"⚠️ Ordner '{IMAGES_FOLDER}' wurde erstellt. Bitte Bilder für den `delete`-Befehl hinzufügen.", target_channel_ids=[cfg.BOT_AUDIT_ID])

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask-Server-Thread gestartet für Health Checks.")

    log_channel_names_to_check = {}
    if cfg.LOG_CHANNEL_ID: log_channel_names_to_check[cfg.LOG_CHANNEL_ID] = "Primär-Log"
    if cfg.BOT_AUDIT_ID: log_channel_names_to_check[cfg.BOT_AUDIT_ID] = "Audit-Log"

    for cid, cname in log_channel_names_to_check.items():
        try:
            ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
            if not ch:
                logger.warning(f"WICHTIG: {cname}-Kanal (ID: {cid}) konnte beim Start nicht gefunden werden.")
        except Exception as e_ch_check:
            logger.error(f"WICHTIG: Fehler beim Überprüfen des {cname}-Kanals (ID: {cid}): {e_ch_check}", exc_info=True)

    try:
        # Sync application commands globally
        synced_commands = await bot.tree.sync()
        num_synced = len(synced_commands) if synced_commands else 0
        command_names = [cmd.name for cmd in synced_commands] if synced_commands else []
        logger.info(f"{num_synced} Befehle global synchronisiert: {command_names}")


        if cfg.TESTING:
            await send_log_message(
                f"✅ Bot version {BOT_VERSION} gestartet und einsatzbereit.",
                target_channel_ids=[TESTING_CHANNEL_ID]
            )
        else:
            await send_log_message(
                f"✅ Bot version {BOT_VERSION} gestartet und einsatzbereit.",
                target_channel_ids=[cfg.LOG_CHANNEL_ID, cfg.BOT_AUDIT_ID]
            )
        sync_info_msg = f"{num_synced} Befehle global synchronisiert: {command_names}"
        await send_log_message(
            f"ℹ️ {sync_info_msg}",
            target_channel_ids=[cfg.BOT_AUDIT_ID]
        )

    except Exception as e:
        logger.error(f"Fehler beim Synchronisieren der Befehle: {e}", exc_info=True)
        await send_log_message(
            f"⚠️ Bot gestartet, aber Fehler beim Synchronisieren der Befehle: {e}",
            target_channel_ids=[cfg.LOG_CHANNEL_ID, cfg.BOT_AUDIT_ID]
        )

    # Initialize garmin_manager after bot is ready
    global garmin_manager
    try:
        # Lazy import to avoid import errors during test discovery when voice-recv extension is unavailable
        from garmin_voice import GarminVoiceManager
        # Lazy import to avoid import errors during test discovery when voice-recv extension is unavailable
        from garmin_voice import GarminVoiceManager
        garmin_manager = GarminVoiceManager(bot)
        logger.info("GarminVoiceManager initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize GarminVoiceManager: {e}", exc_info=True)
        garmin_manager = None

    if not msg_purge_task.is_running():
        msg_purge_task.start()
        logger.info("msg_purge_task gestartet.")

    # Ensure data directory exists before initializing DB or loading settings
    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info(f"Ensured data directory '{DATA_DIR}' exists.")

    init_user_log_db() # Ensures DB tables are ready

    # ---- Verification of database file after init ----
    logger.info(f"Verifying database file at {DATABASE_PATH} after initialization...")
    if os.path.exists(DATABASE_PATH):
        try:
            db_size = os.path.getsize(DATABASE_PATH)
            logger.info(f"Database file {DATABASE_PATH} exists. Size: {db_size} bytes.")
            if db_size == 0:
                logger.warning(f"WARNING: Database file {DATABASE_PATH} is 0 bytes after initialization. This may indicate problems with table creation or disk persistence.")
        except OSError as e:
            logger.error(f"Error accessing database file {DATABASE_PATH} to check size: {e}", exc_info=True)
    else:
        logger.error(f"CRITICAL: Database file {DATABASE_PATH} does NOT exist after initialization attempt. Settings and other DB operations will likely fail.")
    # ---- End of database file verification ----

    # Load all settings from DB, potentially overriding ENV VARs or hardcoded defaults
    cfg.load_all_settings()
    
    # Run initial cleanup on startup
    try:
        logger.info("Running initial audio cleanup on startup...")
        aligned_deleted, garmin_deleted = run_cleanup()
        if aligned_deleted > 0 or garmin_deleted > 0:
            logger.info(f"Initial cleanup completed: {aligned_deleted} aligned files, {garmin_deleted} garmin files deleted")
        else:
            logger.info("Initial cleanup completed: no files to delete")
    except Exception as e:
        logger.error(f"Error during initial cleanup on startup: {e}", exc_info=True)
    
    # Start periodic cleanup task
    if not periodic_cleanup_task.is_running():
        periodic_cleanup_task.start()
        logger.info("Periodic cleanup task started.")

    # Re-evaluate TESTING-dependent channel IDs after loading from DB
    if cfg.TESTING:
        logger.info(f"TESTING MODE ACTIVE (from DB or ENV): Overriding LOG_CHANNEL_ID and BOT_AUDIT_ID to {TESTING_CHANNEL_ID}.")
        cfg.LOG_CHANNEL_ID = TESTING_CHANNEL_ID
        cfg.BOT_AUDIT_ID = TESTING_CHANNEL_ID
    else:
        # If not testing, ensure the original values are loaded from the config
        cfg.load_all_settings()
        logger.info(f"TESTING MODE INACTIVE (from DB or ENV). LOG_CHANNEL_ID: {cfg.LOG_CHANNEL_ID}, BOT_AUDIT_ID: {cfg.BOT_AUDIT_ID}.")
    
    # Scan existing threads for activity before fully starting other tasks
    await scan_existing_threads() 

    await asyncio.sleep(5) # Wait for 5 seconds for cache to populate
    logger.info("Populating initial USERS list...")

    USERS = await get_user_list()
    formatted_users = [f"***{u}***" for u in USERS]
    user_list_msg = f"👥 {len(USERS)} Nutzer online (beim Start): {', '.join(formatted_users) if USERS else 'keine'}"
    await send_log_message(user_list_msg, target_channel_ids=[cfg.LOG_CHANNEL_ID])
    logger.info(f"Sent initial user list to log channel: {user_list_msg}")

    # Check for existing users in monitored channels and auto-join if enabled
    if cfg.GARMIN_AUTO_JOIN_ENABLED:
        logger.info(f"Auto-join enabled. Checking monitored channels: {cfg.GARMIN_AUTO_JOIN_CHANNELS}")
        guild = bot.get_guild(DISCORD_SERVER_ID)
        if guild:
            for channel_id in cfg.GARMIN_AUTO_JOIN_CHANNELS:
                channel = guild.get_channel(channel_id)
                if channel and isinstance(channel, discord.VoiceChannel):
                    # Check if there are non-bot users in the channel
                    non_bot_users = [member for member in channel.members if not member.bot]
                    if non_bot_users:
                        logger.info(f"Found {len(non_bot_users)} users in monitored channel {channel.name} (ID: {channel_id}), auto-joining")
                        try:
                            if garmin_manager is not None:
                                await garmin_manager.join_channel(channel)
                                logger.info(f"Successfully auto-joined channel {channel.name} on startup")
                                break  # Only join the first channel with users
                            else:
                                logger.warning("Garmin manager not available for auto-join")
                        except Exception as e:
                            logger.error(f"Failed to auto-join channel {channel.name} on startup: {e}")
                    else:
                        logger.info(f"No users found in monitored channel {channel.name} (ID: {channel_id})")
        else:
            logger.warning("Could not fetch guild for auto-join check")
    else:
        logger.info("Auto-join disabled, skipping startup channel check")

    try:
        tech_support_forum = bot.get_channel(cfg.TECHSUPPORT_CHANNEL_ID) or await bot.fetch_channel(cfg.TECHSUPPORT_CHANNEL_ID)
        if isinstance(tech_support_forum, discord.ForumChannel):
            closed_tag_obj_on_ready = await get_forum_tag_by_name(tech_support_forum, CLOSED_TAG_NAME)
            if not closed_tag_obj_on_ready:
                await send_log_message(f"⚠️ WICHTIG: Der Tag '{CLOSED_TAG_NAME}' konnte im Forum '{tech_support_forum.name}' (ID: {tech_support_forum.id}) nicht gefunden werden. Die automatische Schließung per Tag funktioniert nicht korrekt.", target_channel_ids=[cfg.BOT_AUDIT_ID])
        elif tech_support_forum:
            await send_log_message(f"⚠️ Tech-Support-Kanal {cfg.TECHSUPPORT_CHANNEL_ID} ('{tech_support_forum.name}') ist kein Forum-Kanal.", target_channel_ids=[cfg.BOT_AUDIT_ID])
        else:
            await send_log_message(f"⚠️ Tech-Support-Kanal {cfg.TECHSUPPORT_CHANNEL_ID} konnte nicht gefunden werden.", target_channel_ids=[cfg.BOT_AUDIT_ID])
    except Exception as e:
        logger.error(f"Fehler bei der initialen Prüfung des Tech-Support-Forums (on_ready): {e}", exc_info=True)
        await send_log_message(f"⚠️ Fehler bei der initialen Prüfung des Tech-Support-Forums (on_ready): {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])


async def get_user_list():
    logger.info(f"Attempting to fetch guild with ID: {DISCORD_SERVER_ID}")
    guild = bot.get_guild(DISCORD_SERVER_ID)
    if guild:
        logger.info(f"Successfully fetched guild: {guild.name} (ID: {guild.id})")
        USERS.clear()
        for vc in guild.voice_channels:
            if vc.id not in cfg.HIDDEN_CHANNELS:
                # Log the voice channel being processed
                logger.info(f"Processing voice channel: {vc.name} (ID: {vc.id}), Member count: {len(vc.members)}")
                for member in vc.members:
                    if not member.bot:
                        # Log the member being added
                        logger.info(f"Found member: {member.name} (ID: {member.id}) in VC {vc.name}")
                        if member.name not in USERS: # Ensure no duplicates
                            USERS.append(member.name)
                            logger.info(f"Added member to USERS list: {member.name}")
                        else:
                            logger.info(f"Member already in USERS list: {member.name}")
                    else:
                        logger.info(f"Skipping bot member: {member.name} (ID: {member.id}) in VC {vc.name}")
        USERS.sort()
        logger.info(f"USERS list populated: {USERS}")
    else:
        logger.warning(f"Could not find guild with ID {DISCORD_SERVER_ID}. User list will be empty.")
        USERS.clear() # Ensure USERS is empty if guild not found

    return USERS


@bot.hybrid_command(name="close", description="Schließt den aktuellen Support-Thread.")
async def close(ctx: commands.Context):
    if not (isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id == cfg.TECHSUPPORT_CHANNEL_ID):
        await ctx.send("Dieser Befehl kann nur in einem Support-Thread des Tech-Support-Forums verwendet werden.", ephemeral=True)
        return
    thread = ctx.channel
    forum_channel = thread.parent
    if not isinstance(forum_channel, discord.ForumChannel):
        await ctx.send("Fehler: Der übergeordnete Kanal ist kein Forum-Kanal. Kann den Tag nicht verwalten.", ephemeral=True)
        return
    closed_tag_object = await get_forum_tag_by_name(forum_channel, CLOSED_TAG_NAME)
    if not closed_tag_object:
        await ctx.send(f"Warnung: Der Tag '{CLOSED_TAG_NAME}' wurde im Forum nicht gefunden. Der Thread wird gesperrt und archiviert, aber der Tag kann nicht gesetzt werden.", ephemeral=True)
        await send_log_message(f"⚠️ Warnung bei Befehl `close` in Thread '{thread.name}': Tag '{CLOSED_TAG_NAME}' im Forum nicht gefunden.", target_channel_ids=[cfg.BOT_AUDIT_ID])
    
    has_closed_tag = any(tag.id == closed_tag_object.id for tag in thread.applied_tags) if closed_tag_object else False
    
    already_fully_closed = thread.locked and (has_closed_tag if closed_tag_object else True) and thread.archived
    if already_fully_closed:
        await ctx.send("Dieser Thread ist bereits als geschlossen markiert (gesperrt, getaggt und archiviert).", ephemeral=True)
        return

    if thread.locked and (has_closed_tag if closed_tag_object else True) and not thread.archived:
        await ctx.send("Dieser Thread ist bereits gesperrt und getaggt, wird nun zusätzlich archiviert.", ephemeral=True)
        try:
            await thread.edit(archived=True)
            await send_log_message(f"ℹ️ Thread '{thread.name}' war gesperrt/getagged, aber nicht archiviert. Jetzt archiviert nach `close`-Befehl von {ctx.author.mention}.", target_channel_ids=[cfg.BOT_AUDIT_ID])
        except Exception as e:
            await send_log_message(f"⚠️ Fehler beim erneuten Archivieren von Thread '{thread.name}': {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])
        return

    trigger_name = ctx.author.mention if ctx.author else "einem unbekannten Benutzer"
    trigger = f"Befehl `/{ctx.invoked_with}` von {trigger_name}" if ctx.interaction else f"Befehl `{bot.command_prefix}{ctx.invoked_with}` von {trigger_name}"
    
    await ctx.send("Der Schließvorgang für den Thread wird eingeleitet...", ephemeral=True)
    await close_support_thread(thread, trigger_source=trigger, set_tag=True)


@bot.hybrid_command(name="delete", description="Sendet eine Info-Nachricht und löscht dann Nachrichten im aktuellen Kanal.")
@commands.has_permissions(manage_messages=True)
@commands.guild_only()
async def delete(ctx: commands.Context, anzahl: int):
    if not (0 < anzahl <= 50):
        await ctx.send("Bitte gib eine Zahl zwischen 1 und 50 für die zu löschenden Nachrichten an.", ephemeral=True)
        return

    target_channel = ctx.channel
    if not isinstance(target_channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
        await ctx.send("Dieser Befehl kann nur in Textkanälen, Voice-Kanal-Chats oder Threads verwendet werden.", ephemeral=True)
        return

    image_file_to_send = None
    image_name_for_embed = None
    try:
        available_images = [f for f in os.listdir(IMAGES_FOLDER) if os.path.isfile(os.path.join(IMAGES_FOLDER, f))]
        if not available_images:
            await ctx.send(f"Keine Bilder im Ordner '{IMAGES_FOLDER}' gefunden. Bitte füge welche hinzu.", ephemeral=True)
            await send_log_message(f"⚠️ Versuchter `delete`-Befehl, aber keine Bilder in '{IMAGES_FOLDER}' durch {ctx.author.mention} in #{target_channel.name}.", target_channel_ids=[cfg.BOT_AUDIT_ID])
            return
        chosen_image_name = random.choice(available_images)
        image_path = os.path.join(IMAGES_FOLDER, chosen_image_name)
        image_file_to_send = discord.File(image_path, filename=chosen_image_name)
        image_name_for_embed = chosen_image_name
    except FileNotFoundError:
        await ctx.send(f"Fehler: Der Bilderordner '{IMAGES_FOLDER}' wurde nicht gefunden.", ephemeral=True)
        await send_log_message(f"⚠️ Bilderordner '{IMAGES_FOLDER}' nicht gefunden bei `delete`-Befehl durch {ctx.author.mention} in #{target_channel.name}.", target_channel_ids=[cfg.BOT_AUDIT_ID])
        return
    except Exception as e:
        await ctx.send("Ein Fehler ist bei der Bildauswahl aufgetreten.", ephemeral=True)
        await send_log_message(f"⚠️ Fehler bei Bildauswahl für `delete` durch {ctx.author.mention} in #{target_channel.name}: {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])
        return

    embed = Embed(description="Delet this", color=discord.Color.blue())
    if image_name_for_embed:
         embed.set_image(url=f"attachment://{image_name_for_embed}")

    info_message = None
    try:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"Info-Nachricht wird gesendet und {anzahl} vorherige Nachrichten werden gelöscht...", ephemeral=True, delete_after=10)
        else: 
            await ctx.send(f"Info-Nachricht wird gesendet und {anzahl} vorherige Nachrichten werden gelöscht...", delete_after=10)
    except discord.HTTPException as e:
         logger.warning(f"Konnte die temporäre Bestätigungsnachricht für delete nicht senden: {e}")


    try:
        info_message = await target_channel.send(file=image_file_to_send, embed=embed)
    except discord.Forbidden:
        err_msg_user = "Ich habe keine Berechtigung, Nachrichten oder Bilder in diesem Kanal zu senden."
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await ctx.send(err_msg_user, delete_after=15)
        await send_log_message(f"⚠️ Keine Sende-Berechtigung für `delete`-Info in #{target_channel.name} (Versuch von {ctx.author.mention}).", target_channel_ids=[cfg.BOT_AUDIT_ID])
        return
    except Exception as e:
        err_msg_user = f"Ein Fehler ist beim Senden der Info-Nachricht aufgetreten: {e}"
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await ctx.send(err_msg_user, delete_after=15)
        await send_log_message(f"⚠️ Fehler beim Senden der `delete`-Info in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])
        return

    deleted_messages_count = 0
    try:
        def check(m):
            is_command_message = False
            if ctx.message: 
                 is_command_message = (m.id == ctx.message.id)
            elif ctx.interaction and ctx.interaction.message: 
                 is_command_message = (m.id == ctx.interaction.message.id)
            
            is_info_message = (info_message and m.id == info_message.id)
            return not is_command_message and not is_info_message

        before_message = info_message 
        if ctx.message: 
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                logger.warning(f"Konnte die Befehlsnachricht von {ctx.author} nicht löschen.")


        deleted_messages = await target_channel.purge(limit=anzahl, check=check, before=before_message)
        deleted_messages_count = len(deleted_messages)
        
        log_msg_text = f"🗑️ {deleted_messages_count} Nachrichten in Kanal #{target_channel.name} (ID: {target_channel.id}) durch {ctx.author.mention} gelöscht (nach Info-Post)."
        await send_log_message(log_msg_text, target_channel_ids=[cfg.BOT_AUDIT_ID])
        logger.info(f"{deleted_messages_count} Nachrichten in #{target_channel.name} durch {ctx.author} gelöscht.")
    except discord.Forbidden:
        err_msg_user = "Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu löschen."
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Keine Lösch-Berechtigung in #{target_channel.name} (Versuch von {ctx.author.mention}).", target_channel_ids=[cfg.BOT_AUDIT_ID])
    except discord.HTTPException as e:
        err_msg_user = f"Ein Fehler ist beim Löschen der Nachrichten aufgetreten: {e.text if e.text else e.status}"
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Fehler beim Löschen in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])
    except Exception as e:
        err_msg_user = f"Ein generischer Fehler ist beim Löschen der Nachrichten aufgetreten: {e}"
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Generischer Fehler beim Löschen in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}", target_channel_ids=[cfg.BOT_AUDIT_ID])


@delete.error
async def delete_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Du hast nicht die erforderlichen Berechtigungen, um diesen Befehl auszuführen.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Dieser Befehl kann nicht in privaten Nachrichten verwendet werden.", ephemeral=True)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Der Parameter `{error.param.name}` fehlt. Bitte gib die Anzahl der zu löschenden Nachrichten an (1-50).", ephemeral=True)
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.HTTPException) and error.original.status == 404:
        await ctx.send("Fehler: Der Kanal konnte nicht gefunden oder Nachrichten darin nicht gelöscht werden (HTTP 404).", ephemeral=True)
    else:
        await ctx.send(f"Ein Fehler ist im `delete`-Befehl aufgetreten: {error}", ephemeral=True)
    logger.error(f"Fehler im delete-Befehl von {ctx.author}: {error}", exc_info=True) # exc_info für Traceback
    await send_log_message(f"⚠️ Fehler im delete-Befehl von {ctx.author} in #{ctx.channel.name if ctx.channel else 'Unbekannter Kanal'}: {error}", target_channel_ids=[cfg.BOT_AUDIT_ID])


@bot.hybrid_command(name="users", description="Listet alle Benutzer in den sichtbaren Voice-Channels auf.")
@commands.guild_only()
async def users(ctx: commands.Context):
    output_lines = ["Aktive Benutzer in Voice-Channels:"]
    any_users_found = False
    if not ctx.guild:
        await ctx.send("Dieser Befehl muss auf einem Server ausgeführt werden.", ephemeral=True)
        return

    for vc in ctx.guild.voice_channels:
        if vc.id in HIDDEN_CHANNELS:
            continue
        current_channel_users = []
        for member in vc.members:
            if not member.bot:
                current_channel_users.append(member.display_name)
        if current_channel_users:
            any_users_found = True
            output_lines.append(f"\n🔊 **{vc.name}** ({len(current_channel_users)}):")
            output_lines.append(", ".join(sorted(current_channel_users)))
    
    if not any_users_found:
        output_lines.append("\nZurzeit sind keine Benutzer in sichtbaren Voice-Channels.")
    
    message_to_send = "\n".join(output_lines)
    
    if len(message_to_send) > 1980: 
        if ctx.interaction: await ctx.interaction.response.defer(ephemeral=False)
        else: await ctx.defer() 

        parts = []
        current_part = ""
        for line in output_lines:
            if len(current_part) + len(line) + 1 > 1980:
                parts.append(current_part)
                current_part = line
            else:
                if current_part: current_part += "\n" + line
                else: current_part = line
        if current_part: parts.append(current_part)
        
        first_sent = False
        for i, part_msg in enumerate(parts):
            if not first_sent:
                if ctx.interaction: await ctx.followup.send(part_msg, ephemeral=False)
                else: await ctx.send(part_msg)
                first_sent = True
            else:
                if ctx.interaction: await ctx.followup.send(part_msg, ephemeral=False)
                else: await ctx.channel.send(part_msg) 
    else:
        if ctx.interaction: await ctx.interaction.response.send_message(message_to_send, ephemeral=False)
        else: await ctx.send(message_to_send)

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot:
        return

    # Process commands first
    if message.guild and message.guild.id == DISCORD_SERVER_ID: # Ensure commands are processed for the correct guild
        await bot.process_commands(message) # Important: process commands before other message handling

    # --- New Thread Activity Tracking Logic ---
    if message.guild and message.guild.id == DISCORD_SERVER_ID and \
       isinstance(message.channel, discord.Thread) and \
       message.channel.parent_id == cfg.TECHSUPPORT_CHANNEL_ID:
        
        thread: discord.Thread = message.channel
        logger.debug(f"Message received in relevant support thread: {thread.name} (ID: {thread.id}) by {message.author.name}")

        # Check if thread is already closed
        if isinstance(thread.parent, discord.ForumChannel):
            closed_tag_object = await get_forum_tag_by_name(thread.parent, CLOSED_TAG_NAME)
            if closed_tag_object and closed_tag_object in thread.applied_tags:
                logger.info(f"Activity in already closed thread '{thread.name}' (ID: {thread.id}). No activity update.")
                return # Do not update activity for closed threads

        op_user_id = None
        if thread.owner_id:
            op_user_id = thread.owner_id
            logger.debug(f"OP user ID for thread {thread.id} from owner_id: {op_user_id}")
        else:
            try:
                # Fallback: fetch starter message if owner_id is None (e.g., user left)
                # The thread ID itself is the ID of the starter message in forum post threads
                starter_message = await thread.fetch_message(thread.id) 
                if starter_message:
                    op_user_id = starter_message.author.id
                    logger.info(f"OP user ID for thread {thread.id} from fetched starter message: {op_user_id}")
                else:
                    logger.warning(f"Could not fetch starter message for thread {thread.id} to determine OP. op_user_id will be None.")
            except discord.NotFound:
                logger.warning(f"Starter message for thread {thread.id} not found. op_user_id will be None.")
            except discord.Forbidden:
                logger.warning(f"Forbidden to fetch starter message for thread {thread.id}. op_user_id will be None.")
            except Exception as e:
                logger.error(f"Error fetching starter message for thread {thread.id}: {e}", exc_info=True)
        
        if not op_user_id:
            logger.error(f"Failed to determine OP user ID for thread {thread.id}. Cannot update activity.")
            # Optionally, send an audit log message about this failure
            # await send_log_message(f"⚠️ Failed to determine OP user ID for thread {thread.id}. Activity not tracked.", target_channel_ids=[cfg.BOT_AUDIT_ID])
            return

        last_message_user_id = message.author.id
        current_timestamp_iso = message.created_at.isoformat() # discord.Message.created_at is already timezone-aware (UTC)

        logger.info(f"Activity detected in thread '{thread.name}' (ID: {thread.id}). Updating timestamp. OP: {op_user_id}, Last Poster: {last_message_user_id}")
        add_or_update_thread_activity(
            thread_id=thread.id,
            guild_id=thread.guild.id,
            last_activity_timestamp_iso=current_timestamp_iso,
            op_user_id=op_user_id,
            last_message_user_id=last_message_user_id
        )
    # --- End of New Thread Activity Tracking Logic ---

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Handle voice state updates for logging and automatic Garmin joining."""
    # Skip bot's own voice state changes
    if member.bot:
        return

    # Get target channel and hidden channels
    target = TESTING_CHANNEL_ID if getattr(cfg, 'TESTING', False) else cfg.LOG_CHANNEL_ID
    hidden = set(getattr(cfg, 'HIDDEN_CHANNELS', []) or [])
    
    # Determine channel states
    before_is_hidden = before.channel and before.channel.id in hidden
    after_is_hidden = after.channel and after.channel.id in hidden
    
    # Determine what type of event occurred
    joined_visible_channel = after.channel and not after_is_hidden and \
                             (not before.channel or before_is_hidden)
    
    left_visible_channel = before.channel and not before_is_hidden and \
                           (not after.channel or after_is_hidden)
    
    switched_between_visible_channels = before.channel and not before_is_hidden and \
                                       after.channel and not after_is_hidden and \
                                       before.channel.id != after.channel.id
    
    # Handle join events
    if joined_visible_channel:
        log_voice_event(member.id, member.name, after.channel.id, after.channel.name, 'join')
        # Send join message
        try:
            msg = f"➕ **{member.name}** ist {after.channel.mention} beigetreten"
            logger.info(f"Sending immediate join message to {target}: {msg}")
            await send_log_message(msg, target_channel_ids=[target])
        except Exception as e:
            logger.error(f"Failed to send immediate join message: {e}", exc_info=True)
        
        # Schedule or reset the global summary timer
        _start_or_reset_global_join_summary_timer()
        
        # Garmin auto-join logic
        if cfg.GARMIN_AUTO_JOIN_ENABLED and after.channel.id in cfg.GARMIN_AUTO_JOIN_CHANNELS and garmin_manager is not None:
            logger.info(f"User {member.name} joined monitored channel {after.channel.name} (ID: {after.channel.id})")
            if not garmin_manager.is_connected():
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await garmin_manager.join_channel(after.channel)
                        logger.info(f"Auto-joined channel {after.channel.name} due to user {member.name} joining")
                        break
                    except Exception as e:
                        logger.error(f"Failed to auto-join channel {after.channel.name} (attempt {attempt + 1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1.0)
                        else:
                            logger.error(f"Failed to auto-join channel {after.channel.name} after {max_retries} attempts")
            else:
                logger.info("Bot is already connected to a voice channel, skipping auto-join")

    # Handle leave events
    elif left_visible_channel:
        log_voice_event(member.id, member.name, before.channel.id, before.channel.name, 'leave')
        # Send leave message
        try:
            msg = f"➖ **{member.name}** hat {before.channel.mention} verlassen"
            logger.info(f"Sending immediate leave message to {target}: {msg}")
            await send_log_message(msg, target_channel_ids=[target])
        except Exception as e:
            logger.error(f"Failed to send immediate leave message: {e}", exc_info=True)
        
        # Schedule or reset the global summary timer
        _start_or_reset_global_join_summary_timer()
        
        # Garmin auto-leave logic
        if cfg.GARMIN_AUTO_JOIN_ENABLED and before.channel.id in cfg.GARMIN_AUTO_JOIN_CHANNELS and garmin_manager is not None:
            remaining_users = [m for m in before.channel.members if not m.bot]
            if (not remaining_users and 
                garmin_manager.is_connected() and 
                garmin_manager.vc and 
                garmin_manager.vc.channel and 
                garmin_manager.vc.channel.id == before.channel.id):
                logger.info(f"All users left channel {before.channel.name}, leaving voice channel")
                try:
                    await garmin_manager.leave_channel()
                except Exception as e:
                    logger.error(f"Failed to leave channel {before.channel.name}: {e}")
            elif not remaining_users:
                logger.debug(f"Users left channel {before.channel.name}, but bot is not in this channel - staying put")
        
        # Cancel AFK timer if user left voice channel
        if member.id in fully_deafened_users:
            fully_deafened_users[member.id].cancel()
            del fully_deafened_users[member.id]
            logger.info(f"AFK Mover: Cancelled timer for {member.name} (left voice channel)")

    # Handle channel switches (no message sent)
    elif switched_between_visible_channels:
        logger.debug(f"User {member.name} switched from {before.channel.name} to {after.channel.name} (no message sent)")
        # Still log the event but don't send messages
        log_voice_event(member.id, member.name, after.channel.id, after.channel.name, 'switch')
        
        # Schedule or reset the global summary timer
        _start_or_reset_global_join_summary_timer()
        
        # Garmin auto-join logic for the new channel
        if cfg.GARMIN_AUTO_JOIN_ENABLED and after.channel.id in cfg.GARMIN_AUTO_JOIN_CHANNELS and garmin_manager is not None:
            logger.info(f"User {member.name} switched to monitored channel {after.channel.name} (ID: {after.channel.id})")
            if not garmin_manager.is_connected():
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await garmin_manager.join_channel(after.channel)
                        logger.info(f"Auto-joined channel {after.channel.name} due to user {member.name} switching")
                        break
                    except Exception as e:
                        logger.error(f"Failed to auto-join channel {after.channel.name} (attempt {attempt + 1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1.0)
                        else:
                            logger.error(f"Failed to auto-join channel {after.channel.name} after {max_retries} attempts")
            else:
                logger.info("Bot is already connected to a voice channel, skipping auto-join")

    # Handle AFK timer for deafened users
    # Check if user became deafened
    if (after.channel and 
        after.self_deaf and 
        not before.self_deaf and 
        member.id not in fully_deafened_users):
        # Start AFK timer for newly deafened user
        task = asyncio.create_task(move_to_afk(member))
        fully_deafened_users[member.id] = task
        logger.info(f"AFK Mover: Started timer for {member.name} (became deafened)")
    
    # Check if user became undeafened
    elif (after.channel and 
          not after.self_deaf and 
          before.self_deaf and 
          member.id in fully_deafened_users):
        # Cancel AFK timer for newly undeafened user
        fully_deafened_users[member.id].cancel()
        logger.info(f"AFK Mover: Cancelled timer for {member.name} (became undeafened)")
        del fully_deafened_users[member.id]

@bot.hybrid_command(name="viewlogs", description="Zeigt die letzten 10 Benutzer-Join-Events an (nur für Admins).")
@commands.has_permissions(administrator=True)
@commands.guild_only()
async def viewlogs(ctx: commands.Context):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        # Fetch last 10 records, ordering by id descending to get the latest entries
        cursor.execute("SELECT user_id, username, channel_id, channel_name, event_type, timestamp FROM user_voice_events ORDER BY id DESC LIMIT 10")
        records = cursor.fetchall()

        if not records:
            await ctx.send("Noch keine Join-Events in der Datenbank vorhanden.", ephemeral=True)
            return

        response_lines = ["**Letzte 10 Benutzer-Join-Events:**"]
        for record in records:
            user_id, username, channel_id, channel_name, event_type, timestamp_str = record
            # Parse ISO timestamp string back to datetime object for formatting (optional, but nice)
            try:
                dt_obj = datetime.datetime.fromisoformat(timestamp_str)
                formatted_timestamp = dt_obj.strftime('%Y-%m-%d %H:%M:%S UTC')
            except ValueError:
                formatted_timestamp = timestamp_str # Fallback if parsing fails

            action = "verlassen" if event_type == "leave" else "beitreten"
            response_lines.append(
                f"Benutzer: {username} ({user_id}) ist dem Kanal {channel_name} ({channel_id}) {action} um {formatted_timestamp}"
            )
        
        response_message = "\n".join(response_lines)

        # Discord message length limit is 2000 characters.
        # For 10 records, this should be fine. If it could be longer, chunking or file sending is needed.
        if len(response_message) > 1980: # Leave some buffer
            # Simple truncation for this example if too long, ideally send as file or multiple messages
            # For now, just send what fits or an error.
            # A better approach for very long messages would be to send as a discord.File
            await ctx.send("Die Log-Nachricht ist zu lang. Hier sind die ersten ~2000 Zeichen:\n" + response_message[:1950], ephemeral=True)
            # Alternative: send as file
            # with open("join_logs.txt", "w", encoding="utf-8") as f:
            # f.write(response_message)
            # await ctx.send(file=discord.File("join_logs.txt"), ephemeral=True)
            # os.remove("join_logs.txt")

        else:
            await ctx.send(response_message, ephemeral=True)

    except sqlite3.Error as e:
        logger.error(f"SQLite error when trying to view logs: {e}")
        await ctx.send(f"Ein Datenbankfehler ist aufgetreten: {e}", ephemeral=True)
    except Exception as e:
        logger.error(f"Generischer Fehler in viewlogs: {e}", exc_info=True)
        await ctx.send(f"Ein unerwarteter Fehler ist aufgetreten: {e}", ephemeral=True)
    finally:
        if conn:
            conn.close()

@viewlogs.error
async def viewlogs_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Du hast nicht die erforderlichen Berechtigungen, um diesen Befehl auszuführen.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Dieser Befehl kann nicht in privaten Nachrichten verwendet werden.", ephemeral=True)
    else:
        await ctx.send(f"Ein Fehler ist im `viewlogs`-Befehl aufgetreten: {error}", ephemeral=True)
        logger.error(f"Fehler im viewlogs-Befehl von {ctx.author}: {error}", exc_info=True)
        await send_log_message(f"⚠️ Fehler im viewlogs-Befehl von {ctx.author} in #{ctx.channel.name if ctx.channel else 'Unbekannter Kanal'}: {error}", target_channel_ids=[cfg.BOT_AUDIT_ID])

# --- Voice State Update ---
# Global Vars for summarized join messages
JOIN_MESSAGE_TIMER_ENABLED = True
JOIN_MESSAGE_TIMER_MINUTES = 7
global_join_summary_timer: Optional[asyncio.Task] = None # Single global timer for all channels

# Global Vars for AFK Mover
fully_deafened_users: Dict[int, asyncio.Task] = {} # Key: user_id, Value: asyncio.Task

async def move_to_afk(member: discord.Member):
    """Coroutine to move a member to the AFK channel after a delay."""
    # Use the configured AFK channel ID and timer from config
    afk_channel_id = cfg.AFK_CHANNEL_ID
    afk_timer_minutes = cfg.AFK_TIMER_MINUTES
    
    if not afk_channel_id:
        logger.warning(f"AFK Mover: AFK_CHANNEL_ID not set. Cannot move {member.name}.")
        return

    logger.info(f"AFK Mover: Starting timer for {member.name} - will move after {afk_timer_minutes} minutes")
    await asyncio.sleep(afk_timer_minutes * 60)

    # Re-fetch member object to ensure we have the latest state
    guild = bot.get_guild(DISCORD_SERVER_ID)
    if not guild: return # Should not happen

    try:
        member = await guild.fetch_member(member.id)
    except discord.NotFound:
        logger.info(f"AFK Mover: {member.name} left the server. No action needed.")
        return # User left, no need to move

    if member.voice and member.voice.channel and member.voice.self_deaf:
        afk_channel = guild.get_channel(afk_channel_id)
        if afk_channel and isinstance(afk_channel, discord.VoiceChannel):
            try:
                await member.move_to(afk_channel, reason=f"Benutzer war für {afk_timer_minutes} Minuten taubgeschaltet.")
                logger.info(f"AFK Mover: Moved {member.name} to AFK channel after {afk_timer_minutes} minutes.")
                await send_log_message(f"😴 {member.mention} wurde in den AFK-Kanal verschoben, da er/sie für {afk_timer_minutes} Minuten taubgeschaltet war.", target_channel_ids=[cfg.BOT_AUDIT_ID])
            except discord.Forbidden:
                logger.error(f"AFK Mover: No permission to move {member.name} to AFK channel.")
            except Exception as e:
                logger.error(f"AFK Mover: Error moving {member.name}: {e}", exc_info=True)
    else:
        logger.info(f"AFK Mover: {member.name} is no longer deafened or in a voice channel. No action needed.")

    # Clean up the task from the tracking dictionary
    if member.id in fully_deafened_users:
        del fully_deafened_users[member.id]

async def send_global_summarized_join_message():
    """Coroutine to send a single global summarized message of all users currently online."""
    try:
        USERS = await get_user_list()
        online_users = [f"***{u}***" for u in USERS]
        message = f"👥 {len(online_users)} Nutzer online: {', '.join(online_users)}"
        target = TESTING_CHANNEL_ID if getattr(cfg, 'TESTING', False) else cfg.LOG_CHANNEL_ID
        await send_log_message(message, target_channel_ids=[target])
        logger.info(f"Sent global summarized join message: {len(online_users)} users online")
    except Exception as e:
        logger.error(f"Error sending global summarized join message: {e}")


async def _global_join_summary_timer_worker(delay_seconds: float):
    """Global timer worker that sends a single summarized message for all channels."""
    try:
        await asyncio.sleep(delay_seconds)
        await send_global_summarized_join_message()
    except asyncio.CancelledError:
        logger.debug("Global join summary timer cancelled")
        return
    except Exception as e:
        logger.error(f"Error in global join summary timer: {e}")
    finally:
        # Clean up the global timer reference
        global global_join_summary_timer
        global_join_summary_timer = None


def _start_or_reset_global_join_summary_timer():
    """Start or reset the global join summary timer. Only one timer runs at a time."""
    enabled = getattr(cfg, 'JOIN_MESSAGE_TIMER_ENABLED', JOIN_MESSAGE_TIMER_ENABLED)
    minutes = getattr(cfg, 'JOIN_MESSAGE_TIMER_MINUTES', JOIN_MESSAGE_TIMER_MINUTES)
    if not enabled:
        return
    
    try:
        global global_join_summary_timer
        
        # Check if global timer is already running
        if global_join_summary_timer and not global_join_summary_timer.done():
            logger.debug("Global join summary timer already running, not starting a new one")
            return  # Don't reset the timer, just let it continue
        
        # Start a new global timer only if none is running
        delay_seconds = max(0.0, float(minutes) * 60.0)
        global_join_summary_timer = asyncio.create_task(_global_join_summary_timer_worker(delay_seconds))
        logger.debug(f"Started global join summary timer in {minutes} minute(s)")
    except Exception as e:
        logger.error(f"Failed to start global join summary timer: {e}")


# Global variable for garmin_manager - will be initialized in on_ready
garmin_manager = None

@bot.hybrid_command(name="garmin_start", description="Starts the Garmin voice recording.")
@commands.guild_only()
async def start_garmin(ctx: commands.Context):
    if garmin_manager is None:
        await ctx.send("❌ Garmin voice system is not available. Please contact an administrator.")
        return
    
    if ctx.author.voice:
        await garmin_manager.join_channel(ctx.author.voice.channel)
        await ctx.send("Garmin voice recording started.")
    else:
        await ctx.send("You need to be in a voice channel to start the Garmin voice recording.")

@bot.hybrid_command(name="garmin_stop", description="Stops the Garmin voice recording.")
@commands.guild_only()
async def stop_garmin(ctx: commands.Context):
    if garmin_manager is None:
        await ctx.send("❌ Garmin voice system is not available. Please contact an administrator.")
        return
    
    await garmin_manager.leave_channel()
    await ctx.send("Garmin voice recording stopped.")

@bot.hybrid_command(name="garmin_save", description="Saves the Garmin voice recording.")
@commands.guild_only()
async def save_garmin(ctx: commands.Context):
    if garmin_manager is None:
        await ctx.send("❌ Garmin voice system is not available. Please contact an administrator.")
        return
    
    try:
        health_data = garmin_manager.get_recording_health()
        
        if health_data["connected"] and health_data["buffer_size"] > 0:
            garmin_manager.save_recording()
            await ctx.send("✅ Garmin voice recording saved successfully.")
        else:
            if not health_data["connected"]:
                await ctx.send("❌ Not connected to any voice channel. Join a channel first with `!!garmin_start`")
            else:
                await ctx.send("❌ No audio data to save. Start recording first with `!!garmin_start`")
    except Exception as e:
        logger.error(f"Error in garmin_save command: {e}", exc_info=True)
        await ctx.send("❌ Error saving recording. Check logs for details.")

@bot.hybrid_command(name="garmin_health", description="Shows the health status of the Garmin voice recording system.")
@commands.guild_only()
async def garmin_health(ctx: commands.Context):
    if garmin_manager is None:
        await ctx.send("❌ Garmin voice system is not available. Please contact an administrator.")
        return
    
    health_data = garmin_manager.get_recording_health()
    
    embed = discord.Embed(
        title="🎙️ Garmin Voice Recording Health",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    
    # Connection status
    status_emoji = "🟢" if health_data["connected"] else "🔴"
    embed.add_field(
        name="Connection Status",
        value=f"{status_emoji} {'Connected' if health_data['connected'] else 'Disconnected'}",
        inline=True
    )
    
    # Recording duration
    duration_str = f"{health_data['recording_duration']:.1f}s" if health_data['recording_duration'] > 0 else "Not recording"
    embed.add_field(
        name="Recording Duration",
        value=duration_str,
        inline=True
    )
    
    # Buffer size
    buffer_mb = health_data['buffer_size'] / (1024 * 1024)
    embed.add_field(
        name="Buffer Size",
        value=f"{buffer_mb:.2f} MB",
        inline=True
    )
    
    # Error count
    error_color = "🟢" if health_data['recording_errors'] == 0 else "🟡" if health_data['recording_errors'] < health_data['max_errors'] else "🔴"
    embed.add_field(
        name="Recording Errors",
        value=f"{error_color} {health_data['recording_errors']}/{health_data['max_errors']}",
        inline=True
    )
    
    # Processing status
    processing_emoji = "🔄" if health_data['is_processing'] else "⏸️"
    embed.add_field(
        name="Processing Status",
        value=f"{processing_emoji} {'Processing' if health_data['is_processing'] else 'Idle'}",
        inline=True
    )
    
    # STT Status
    stt_status = f"{'🟢' if health_data['stt_enabled'] else '🔴'} {health_data['stt_engine'].title()}"
    embed.add_field(
        name="STT Status",
        value=stt_status,
        inline=True
    )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="garmin_autojoin", description="Manage Garmin auto-join feature: status, enable, disable")
@commands.guild_only()
async def garmin_autojoin(ctx: commands.Context, action: str = "status"):
    """
    Manage Garmin auto-join feature.
    
    **Usage:**
    `!!garmin_autojoin [action]`
    
    **Actions:**
    • `status` - Show current auto-join configuration and status
    • `enable` - Enable auto-join feature (bot will automatically join monitored channels)
    • `disable` - Disable auto-join feature (bot will not automatically join channels)
    
    **Examples:**
    • `!!garmin_autojoin status` - Check current status
    • `!!garmin_autojoin enable` - Enable auto-join
    • `!!garmin_autojoin disable` - Disable auto-join
    • `!!garmin_autojoin` - Same as status (default)
    
    **Note:** Changes are saved to the database and persist across bot restarts.
    """
    action = action.lower()
    
    if action not in ["status", "enable", "disable"]:
        embed = discord.Embed(
            title="❌ Invalid Action",
            description="Please use one of the following actions:",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Available Actions", value="• `status` - Show current configuration\n• `enable` - Enable auto-join\n• `disable` - Disable auto-join", inline=False)
        embed.add_field(name="Examples", value="• `!!garmin_autojoin status`\n• `!!garmin_autojoin enable`\n• `!!garmin_autojoin disable`", inline=False)
        await ctx.send(embed=embed)
        return
    
    current_setting = cfg.GARMIN_AUTO_JOIN_ENABLED
    new_setting = current_setting
    action_taken = False
    
    # Handle the action
    if action == "enable" and not current_setting:
        new_setting = True
        action_taken = True
    elif action == "disable" and current_setting:
        new_setting = False
        action_taken = True
    elif action == "status":
        # No change needed, just show status
        pass
    else:
        # Action would not change the current state
        if action == "enable":
            await ctx.send("ℹ️ Auto-join is already enabled.")
        else:  # disable
            await ctx.send("ℹ️ Auto-join is already disabled.")
        return
    
    # Save the setting if it changed
    if action_taken:
        from config_loader import save_setting, DB_KEY_GARMIN_AUTO_JOIN_ENABLED
        save_setting(DB_KEY_GARMIN_AUTO_JOIN_ENABLED, str(new_setting).lower())
        
        # Log the change
        logger.info(f"Garmin auto-join setting changed by {ctx.author.name} ({ctx.author.id}): {current_setting} -> {new_setting}")
        
        # Reload settings to update the global variable
        cfg.load_all_settings()
    
    # Create embed to show the result
    embed = discord.Embed(
        title="🤖 Garmin Auto-Join Configuration",
        color=discord.Color.green() if new_setting else discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    
    # Auto-join status
    status_emoji = "🟢" if new_setting else "🔴"
    status_text = "Enabled" if new_setting else "Disabled"
    embed.add_field(
        name="Auto-Join Status",
        value=f"{status_emoji} {status_text}",
        inline=True
    )
    
    # Add action message if something was done
    if action_taken:
        embed.add_field(
            name="Action",
            value=f"✅ Auto-join has been **{status_text.lower()}**",
            inline=False
        )
    else:
        embed.add_field(
            name="Action",
            value="📊 Showing current status",
            inline=False
        )
    
    # Monitored channels
    if cfg.GARMIN_AUTO_JOIN_CHANNELS:
        guild = bot.get_guild(DISCORD_SERVER_ID)
        channel_names = []
        for channel_id in cfg.GARMIN_AUTO_JOIN_CHANNELS:
            channel = guild.get_channel(channel_id) if guild else None
            if channel:
                channel_names.append(f"#{channel.name} ({channel_id})")
            else:
                channel_names.append(f"Unknown Channel ({channel_id})")
        
        embed.add_field(
            name="Monitored Channels",
            value="\n".join(channel_names),
            inline=False
        )
    else:
        embed.add_field(
            name="Monitored Channels",
            value="No channels configured",
            inline=False
        )
    
    # Current connection status
    if garmin_manager is not None and garmin_manager.is_connected():
        current_channel = garmin_manager.vc.channel
        embed.add_field(
            name="Current Connection",
            value=f"🟢 Connected to #{current_channel.name} ({current_channel.id})",
            inline=False
        )
    else:
        embed.add_field(
            name="Current Connection",
            value="🔴 Not connected to any voice channel",
            inline=False
        )
    
    await ctx.send(embed=embed)

# --------- Daily Inactivity Check Task ---------
@tasks.loop(hours=24) # Set to 24 for production, can be lower for testing (e.g. minutes=1)
async def check_inactive_threads_task():
    logger.info("Starting daily check for inactive threads...")
    now = datetime.datetime.now(datetime.timezone.utc)
    all_tracked_threads = get_all_thread_activities()
    
    if not all_tracked_threads:
        logger.info("No threads currently tracked for inactivity. Task iteration complete.")
        return

    tech_support_forum = None
    if TECHSUPPORT_CHANNEL_ID:
        try:
            tech_support_forum = bot.get_channel(TECHSUPPORT_CHANNEL_ID) or await bot.fetch_channel(TECHSUPPORT_CHANNEL_ID)
            if not isinstance(tech_support_forum, discord.ForumChannel):
                logger.error(f"TECHSUPPORT_CHANNEL_ID {TECHSUPPORT_CHANNEL_ID} is not a ForumChannel. Cannot proceed with inactivity check.")
                tech_support_forum = None # Ensure it's None if not a forum
        except (discord.NotFound, discord.Forbidden) as e:
            logger.error(f"Could not fetch Tech Support Forum (ID: {TECHSUPPORT_CHANNEL_ID}): {e}. Cannot proceed with inactivity check.")
            tech_support_forum = None # Ensure it's None
        except Exception as e:
            logger.error(f"Unexpected error fetching Tech Support Forum (ID: {TECHSUPPORT_CHANNEL_ID}): {e}", exc_info=True)
            tech_support_forum = None # Ensure it's None


    closed_tag_object = None
    if tech_support_forum: # Only try to get tag if forum exists
        try:
            closed_tag_object = await get_forum_tag_by_name(tech_support_forum, CLOSED_TAG_NAME)
            if not closed_tag_object:
                logger.warning(f"'{CLOSED_TAG_NAME}' tag not found in forum '{tech_support_forum.name}'. External closure check by tag will be skipped.")
        except Exception as e:
            logger.error(f"Error getting closed_tag_object for forum '{tech_support_forum.name}': {e}", exc_info=True)


    threads_processed_count = 0
    for record in all_tracked_threads:
        threads_processed_count +=1
        logger.debug(f"Processing record: {dict(record)}") # Log the whole record for easier debugging
        try:
            thread_id = record['thread_id']
            last_activity_timestamp_iso = record['last_activity_timestamp']
            warning_sent_timestamp_iso = record['warning_sent_timestamp']
            reminder_sent_timestamp_iso = record['reminder_sent_timestamp']
            op_user_id = record['op_user_id']
            last_message_user_id = record['last_message_user_id']

            if not last_activity_timestamp_iso:
                logger.warning(f"Thread {thread_id} has no last_activity_timestamp. Skipping.")
                continue # Should not happen with current logic, but good to check

            try:
                last_activity_dt = datetime.datetime.fromisoformat(last_activity_timestamp_iso)
            except ValueError:
                logger.error(f"Invalid ISO format for last_activity_timestamp '{last_activity_timestamp_iso}' for thread {thread_id}. Skipping.")
                continue
            
            warning_sent_dt = None
            if warning_sent_timestamp_iso:
                try:
                    warning_sent_dt = datetime.datetime.fromisoformat(warning_sent_timestamp_iso)
                except ValueError:
                    logger.error(f"Invalid ISO format for warning_sent_timestamp '{warning_sent_timestamp_iso}' for thread {thread_id}. Treating as not sent.")
            
            reminder_sent_dt = None
            if reminder_sent_timestamp_iso:
                try:
                    reminder_sent_dt = datetime.datetime.fromisoformat(reminder_sent_timestamp_iso)
                except ValueError:
                    logger.error(f"Invalid ISO format for reminder_sent_timestamp '{reminder_sent_timestamp_iso}' for thread {thread_id}. Treating as not sent.")

            thread_object: Optional[discord.Thread] = bot.get_channel(thread_id)
            if not thread_object:
                try:
                    thread_object = await bot.fetch_channel(thread_id)
                except discord.NotFound:
                    logger.info(f"Thread {thread_id} not found (deleted?). Removing from tracking.")
                    remove_thread_activity(thread_id)
                    continue
                except discord.Forbidden:
                    logger.warning(f"Forbidden to fetch thread {thread_id}. Cannot check status. Skipping for now.")
                    continue # Skip this iteration, might be a temporary permissions issue
                except Exception as e:
                    logger.error(f"Error fetching thread {thread_id}: {e}. Skipping for now.", exc_info=True)
                    continue
            
            if not isinstance(thread_object, discord.Thread):
                logger.warning(f"Channel {thread_id} is not a Thread. Type: {type(thread_object)}. Removing from tracking.")
                remove_thread_activity(thread_id)
                continue

            # Check for external closure
            is_externally_closed = False
            if thread_object.archived or thread_object.locked:
                is_externally_closed = True
                logger.info(f"Thread '{thread_object.name}' (ID: {thread_id}) found to be archived or locked externally.")
            elif closed_tag_object and closed_tag_object in thread_object.applied_tags:
                is_externally_closed = True
                logger.info(f"Thread '{thread_object.name}' (ID: {thread_id}) found to have '{CLOSED_TAG_NAME}' tag externally.")
            
            if is_externally_closed:
                logger.info(f"Removing externally closed/managed thread '{thread_object.name}' (ID: {thread_id}) from activity tracking.")
                remove_thread_activity(thread_id)
                continue

            # Fetch users for mentions - do this once before stages
            op_user_mention = f"<@{op_user_id}>" # Default to raw mention
            try:
                target_op_user = await bot.fetch_user(op_user_id)
                op_user_mention = target_op_user.mention
            except discord.NotFound:
                logger.warning(f"OP user {op_user_id} for thread {thread_id} not found. Using raw ID for mention.")
            except Exception as e_user:
                logger.error(f"Error fetching OP user {op_user_id} for thread {thread_id}: {e_user}. Using raw ID.", exc_info=True)

            last_msg_user_mention = f"<@{last_message_user_id}>"
            if last_message_user_id != op_user_id : # Avoid double ping if OP was last poster
                try:
                    target_last_msg_user = await bot.fetch_user(last_message_user_id)
                    last_msg_user_mention = target_last_msg_user.mention
                except discord.NotFound:
                    logger.warning(f"Last message user {last_message_user_id} for thread {thread_id} not found. Using raw ID for mention.")
                except Exception as e_user:
                    logger.error(f"Error fetching last message user {last_message_user_id} for thread {thread_id}: {e_user}. Using raw ID.", exc_info=True)
            else: # OP was the last poster
                last_msg_user_mention = "" # Don't ping the same user twice

            mentions = f"{op_user_mention} {last_msg_user_mention}".strip()


            # Stage 3: Closure (after 24h from reminder)
            if reminder_sent_dt and (now - reminder_sent_dt > datetime.timedelta(days=1)): # Check Stage 3 first
                logger.info(f"Thread '{thread_object.name}' (ID: {thread_id}) is due for closure. Last activity: {last_activity_dt}, Reminder: {reminder_sent_dt}.")
                try:
                    await thread_object.send(f"Dieser Thread wurde aufgrund von Inaktivität automatisch geschlossen. {mentions}")
                    await close_support_thread(thread_object, "automatischer Inaktivitäts-Timer")
                    remove_thread_activity(thread_id) # Successfully closed and removed
                    logger.info(f"Thread '{thread_object.name}' (ID: {thread_id}) automatically closed and removed from tracking.")
                except discord.Forbidden:
                    logger.error(f"Forbidden to close or send message in thread '{thread_object.name}' (ID: {thread_id}). Will retry next cycle.")
                except Exception as e_close:
                    logger.error(f"Error during auto-closure of thread '{thread_object.name}' (ID: {thread_id}): {e_close}", exc_info=True)
                continue # Move to next thread record

            # Stage 2: Reminder (after 24h from warning)
            elif warning_sent_dt and not reminder_sent_dt and (now - warning_sent_dt > datetime.timedelta(days=1)):
                logger.info(f"Thread '{thread_object.name}' (ID: {thread_id}) is due for a 24h reminder. Last activity: {last_activity_dt}, Warning: {warning_sent_dt}.")
                try:
                    await thread_object.send(f"Erinnerung: Dieser Thread ist weiterhin inaktiv und wird in 24 Stunden automatisch geschlossen, wenn keine neue Antwort erfolgt. {mentions}")
                    update_thread_reminder_sent(thread_id, now.isoformat())
                    logger.info(f"Sent 24h reminder for thread '{thread_object.name}' (ID: {thread_id}).")
                except discord.Forbidden:
                    logger.error(f"Forbidden to send reminder in thread '{thread_object.name}' (ID: {thread_id}). Will retry next cycle.")
                except Exception as e_remind:
                    logger.error(f"Error sending reminder for thread '{thread_object.name}' (ID: {thread_id}): {e_remind}", exc_info=True)
                continue

            # Stage 1: Warning (after 48h from last activity)
            elif not warning_sent_dt and (now - last_activity_dt > datetime.timedelta(days=2)):
                logger.info(f"Thread '{thread_object.name}' (ID: {thread_id}) is due for a 48h warning. Last activity: {last_activity_dt}.")
                try:
                    await thread_object.send(f"Dieser Thread ist seit 48 Stunden inaktiv und wird in weiteren 48 Stunden automatisch geschlossen, wenn keine neue Antwort erfolgt. {mentions}")
                    update_thread_warning_sent(thread_id, now.isoformat())
                    logger.info(f"Sent 48h warning for thread '{thread_object.name}' (ID: {thread_id}).")
                except discord.Forbidden:
                    logger.error(f"Forbidden to send warning in thread '{thread_object.name}' (ID: {thread_id}). Will retry next cycle.")
                except Exception as e_warn:
                    logger.error(f"Error sending warning for thread '{thread_object.name}' (ID: {thread_id}): {e_warn}", exc_info=True)
                continue
            else:
                logger.debug(f"Thread '{thread_object.name}' (ID: {thread_id}) not yet due for any inactivity action.")

        except Exception as e_outer:
            thread_id_for_log = record.get('thread_id', 'UNKNOWN_ID') if record else 'UNKNOWN_RECORD'
            logger.error(f"Unhandled exception processing thread record for ID {thread_id_for_log} in check_inactive_threads_task: {e_outer}", exc_info=True)
            # Continue to the next record to prevent one bad record from stopping the entire task
            
    logger.info(f"Daily check for inactive threads completed. Processed {threads_processed_count} DB records.")

@check_inactive_threads_task.before_loop
async def before_check_inactive_threads_task():
    await bot.wait_until_ready()
    logger.info("check_inactive_threads_task: Bot is ready, starting loop.")


PURGE_OLDER_THAN_DAYS = 7 # Default value, will be configurable

@tasks.loop(hours=24)
async def msg_purge_task():
    target_ids_task_log = [cfg.BOT_AUDIT_ID] if cfg.BOT_AUDIT_ID else []

    purge_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=cfg.PURGE_OLDER_THAN_DAYS)

    def is_older_than_cutoff(message):
        return message.created_at < purge_cutoff_date

    channels_to_purge_ids = [cfg.LOG_CHANNEL_ID, cfg.BOT_AUDIT_ID]

    for channel_id in channels_to_purge_ids:
        if not channel_id:
            logger.info(f"Purge Task: Skipping a channel because its ID is not configured.")
            continue

        channel_to_purge_obj = bot.get_channel(channel_id)
        if not channel_to_purge_obj:
            try:
                channel_to_purge_obj = await bot.fetch_channel(channel_id)
            except Exception as e_fetch_purge:
                await send_log_message(f"⚠️ Daily Purge: Target channel (ID {channel_id}) for purging not found: {e_fetch_purge}", target_channel_ids=target_ids_task_log)
                continue # Skip to the next channel

        if not isinstance(channel_to_purge_obj, discord.TextChannel):
             logger.warning(f"Purge Task: Channel {channel_id} is not a TextChannel, cannot purge.")
             continue


        await send_log_message(f"🔄 Starting daily purge in channel {channel_to_purge_obj.mention} (ID: {channel_id}) for messages older than {cfg.PURGE_OLDER_THAN_DAYS} days (before {purge_cutoff_date.strftime('%Y-%m-%d %H:%M:%S UTC')}).", target_channel_ids=target_ids_task_log)
        try:
            # Note: purge() can only bulk-delete messages up to 14 days old.
            # This logic will work for PURGE_OLDER_THAN_DAYS <= 14.
            # For messages older than 14 days, they need to be deleted individually, which is much slower.
            # The current implementation relies on the bulk purge behavior.
            if cfg.PURGE_OLDER_THAN_DAYS > 14:
                 await send_log_message(f"⚠️ Daily Purge: Configured purge duration ({cfg.PURGE_OLDER_THAN_DAYS} days) is > 14 days. The bot can only bulk-delete messages up to 14 days old. Purging will be ineffective for older messages.", target_channel_ids=target_ids_task_log)

            deleted_messages = await channel_to_purge_obj.purge(limit=None, check=is_older_than_cutoff, bulk=True)
            if deleted_messages:
                await send_log_message(f"🗑️ Daily Purge: {len(deleted_messages)} messages deleted in {channel_to_purge_obj.mention}.", target_channel_ids=target_ids_task_log)
            else:
                await send_log_message(f"ℹ️ Daily Purge: No messages found in {channel_to_purge_obj.mention} that matched the criteria (older than {cfg.PURGE_OLDER_THAN_DAYS} days and within the last 14 days).", target_channel_ids=target_ids_task_log)
        except discord.Forbidden:
            await send_log_message(f"⚠️ Daily Purge: No permission to delete messages in {channel_to_purge_obj.mention}.", target_channel_ids=target_ids_task_log)
        except discord.HTTPException as e:
            if e.status == 400 and "14 days" in e.text.lower():
                 await send_log_message(f"ℹ️ Daily Purge: Could not delete messages in {channel_to_purge_obj.mention}. Messages are likely all older than 14 days, or none matched. API message: {e.text}", target_channel_ids=target_ids_task_log)
            else:
                 await send_log_message(f"⚠️ Daily Purge HTTP error in {channel_to_purge_obj.mention}: {e}", target_channel_ids=target_ids_task_log)
        except Exception as e:
            await send_log_message(f"⚠️ Daily Purge generic error in {channel_to_purge_obj.mention}: {e}", target_channel_ids=target_ids_task_log)


@tasks.loop(hours=6)
async def periodic_cleanup_task():
    """Periodically clean up old audio files based on configured retention periods."""
    try:
        logger.info("Starting periodic audio cleanup...")
        aligned_deleted, garmin_deleted = run_cleanup()
        
        if aligned_deleted > 0 or garmin_deleted > 0:
            logger.info(f"Periodic cleanup completed: {aligned_deleted} aligned files, {garmin_deleted} garmin files deleted")
        else:
            logger.debug("Periodic cleanup completed: no files to delete")
            
    except Exception as e:
        logger.error(f"Error during periodic cleanup: {e}", exc_info=True)


@bot.event
async def on_thread_update(before: Thread, after: Thread):
    if after.guild.id != DISCORD_SERVER_ID: return
    parent = after.parent
    if parent and parent.id == TECHSUPPORT_CHANNEL_ID:
        if not isinstance(parent, discord.ForumChannel): return 
        
        before_tags_lower = {tag.name.lower() for tag in before.applied_tags}
        after_tags_lower = {tag.name.lower() for tag in after.applied_tags}
        closed_tag_lower = CLOSED_TAG_NAME.lower()

        tag_added = closed_tag_lower in after_tags_lower and closed_tag_lower not in before_tags_lower
        
        if tag_added and (not after.locked or not after.archived):
            await send_log_message(f"ℹ️ Thread '{after.name}' (ID: {after.id}) Tag '{CLOSED_TAG_NAME}' erhalten. Schließe...", target_channel_ids=[cfg.BOT_AUDIT_ID])
            await close_support_thread(after, f"Tag '{CLOSED_TAG_NAME}' manuell hinzugefügt", set_tag=False)


async def graceful_shutdown():
    global shutdown_initiated
    if shutdown_initiated:
        return
    shutdown_initiated = True
    logger.info("Shutdown-Signal empfangen. Beginne graceful shutdown...")

    if msg_purge_task.is_running():
        logger.info("Stoppe msg_purge_task...")
        msg_purge_task.cancel()
        try:
            # msg_purge_task.stop() ist keine Standardmethode für tasks.loop. cancel() ist korrekt.
            # Wir warten hier nicht explizit, da cancel() den Task beim nächsten Durchlauf beendet.
            # await msg_purge_task.stop() # Entfernt oder durch geeignetes Warten ersetzen falls nötig
            pass # cancel() wurde gerufen, das reicht für den Shutdown-Prozess
        except asyncio.CancelledError:
            logger.info("msg_purge_task erfolgreich abgebrochen.")
        except Exception as e:
            logger.error(f"Fehler beim Stoppen von msg_purge_task: {e}", exc_info=True)
    
    if periodic_cleanup_task.is_running():
        logger.info("Stoppe periodic_cleanup_task...")
        periodic_cleanup_task.cancel()
        try:
            pass # cancel() wurde gerufen, das reicht für den Shutdown-Prozess
        except asyncio.CancelledError:
            logger.info("periodic_cleanup_task erfolgreich abgebrochen.")
        except Exception as e:
            logger.error(f"Fehler beim Stoppen von periodic_cleanup_task: {e}", exc_info=True)
    
    logger.info("Sende 'Bot wird gestoppt...' Nachricht (falls möglich).")
    stop_message_targets = []
    if cfg.LOG_CHANNEL_ID: stop_message_targets.append(cfg.LOG_CHANNEL_ID)
    if cfg.BOT_AUDIT_ID: stop_message_targets.append(cfg.BOT_AUDIT_ID)
    
    if stop_message_targets:
        try:
            if bot.is_ready() or (bot.loop and bot.loop.is_running() and not bot.is_closed()):
                await send_log_message(
                    "⏳ Bot wird gestoppt...",
                    target_channel_ids=list(set(stop_message_targets))
                )
                logger.info("'Bot wird gestoppt...' Nachricht gesendet.")
                await asyncio.sleep(0.5) 
            else:
                logger.warning("Bot nicht bereit, 'Bot wird gestoppt...' Nachricht kann nicht gesendet werden.")
        except Exception as e:
            logger.error(f"Fehler beim Senden der 'Bot wird gestoppt...' Nachricht: {e}", exc_info=True)
    
    logger.info("Schließe Bot-Verbindung...")
    if bot.loop and bot.loop.is_running() and not bot.is_closed():
        try:
            await bot.close()
            logger.info("Bot-Verbindung erfolgreich geschlossen.")
        except Exception as e:
            logger.error(f"Fehler beim bot.close(): {e}", exc_info=True)
    else:
        logger.info("Bot-Verbindung war bereits geschlossen oder Loop nicht aktiv.")
    logger.info("Graceful shutdown abgeschlossen.")


def handle_signal(signum, frame):
    global shutdown_initiated
    if shutdown_initiated:
        logger.info(f"Signal {signum} erneut empfangen, Shutdown bereits eingeleitet.")
        return
    
    signal_name = signal.Signals(signum).name if isinstance(signum, int) else str(signum)
    logger.info(f"Signal {signal_name} empfangen. Leite graceful shutdown ein.")
    
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(graceful_shutdown(), bot.loop)
    else:
        logger.warning("Bot-Loop nicht aktiv. Direkter Versuch, Shutdown-Flag zu setzen und zu beenden.")
        if not shutdown_initiated: 
            shutdown_initiated = True

async def main():
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        logger.critical("Fehler: Umgebungsvariable 'DISCORD_TOKEN' ist nicht gesetzt.")
        return

    try:
        logger.info("Starte Bot...")
        await bot.start(TOKEN)
    except discord.LoginFailure:
        logger.critical("Login fehlgeschlagen. Überprüfe den Token.")
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt während bot.start() in main().")
        if not shutdown_initiated:
            await graceful_shutdown()
    except Exception as e:
        logger.critical(f"Unerwarteter Fehler beim Starten oder während der Laufzeit des Bots in main(): {e}", exc_info=True)
        if not shutdown_initiated:
            logger.info("Versuche graceful shutdown nach unerwartetem Fehler in main()...")
            try:
                await graceful_shutdown()
            except Exception as eshutdown:
                logger.error(f"Fehler während des Shutdowns nach Fehler in main(): {eshutdown}", exc_info=True)
    finally:
        if not bot.is_closed() and not shutdown_initiated:
            logger.warning("Bot war noch nicht geschlossen und kein Shutdown eingeleitet. Schließe jetzt...")
            await graceful_shutdown() 
        elif not bot.is_closed() and shutdown_initiated:
             logger.info("Bot war trotz eingeleitetem Shutdown noch nicht geschlossen. Erneuter Versuch via bot.close().")
             if bot.loop and bot.loop.is_running(): # Nur wenn Loop noch läuft
                 await bot.close()
             else:
                 logger.warning("Bot-Loop nicht aktiv, bot.close() im finalen Block übersprungen.")


        logger.info("Bot-Hauptroutine (main) beendet.")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt auf oberster Ebene empfangen. Programm wird beendet.")
    finally:
        logger.info("asyncio.run() wurde beendet. Programm-Aufräumarbeiten abgeschlossen.")
        logger.info("Bot-Prozess wird nun endgültig beendet.")
# Ensure newline at the end of the file