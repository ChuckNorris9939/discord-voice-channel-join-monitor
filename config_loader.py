
import os
import sqlite3
import logging
from typing import Optional, List

logger = logging.getLogger("discord_bot.config")

# --- Database Path ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
DATABASE_NAME = "user_log.db"
DATABASE_PATH = os.path.join(CONFIG_DIR, DATABASE_NAME)

# --------- Helper Functions for bot_settings Table ---------
def get_setting(setting_name: str, default_value: Optional[str] = None) -> Optional[str]:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM bot_settings WHERE setting_name = ?", (setting_name,))
        row = cursor.fetchone()
        if row:
            # logger.debug(f"Setting '{setting_name}' retrieved with value: {row['setting_value']}")
            return row['setting_value']
        else:
            # logger.debug(f"Setting '{setting_name}' not found, returning default value: {default_value}")
            return default_value
    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_setting for '{setting_name}': {e}", exc_info=True)
        return default_value # Return default_value on error as well
    except Exception as e:
        logger.error(f"General error in get_setting for '{setting_name}': {e}", exc_info=True)
        return default_value
    finally:
        if conn:
            conn.close()

def save_setting(setting_name: str, setting_value: str):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_settings (setting_name, setting_value) VALUES (?, ?)", (setting_name, setting_value))
        conn.commit()
        # logger.info(f"Setting '{setting_name}' saved to database with value: {setting_value}")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in save_setting for '{setting_name}': {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in save_setting for '{setting_name}': {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# --------- Settings Loading Function ---------
DB_KEY_APP_TESTING_MODE = "APP_TESTING_MODE"
DB_KEY_HIDDEN_CHANNELS = "HIDDEN_CHANNELS"
DB_KEY_LOG_CHANNEL_ID = "LOG_CHANNEL_ID"
DB_KEY_BOT_AUDIT_ID = "BOT_AUDIT_ID"
DB_KEY_TECHSUPPORT_CHANNEL_ID = "TECHSUPPORT_CHANNEL_ID"
DB_KEY_AFK_CHANNEL_ID = "AFK_CHANNEL_ID"
DB_KEY_PURGE_OLDER_THAN_DAYS = "PURGE_OLDER_THAN_DAYS"
DB_KEY_JOIN_MESSAGE_TIMER_ENABLED = "JOIN_MESSAGE_TIMER_ENABLED"
DB_KEY_JOIN_MESSAGE_TIMER_MINUTES = "JOIN_MESSAGE_TIMER_MINUTES"
DB_KEY_AFK_TIMER_MINUTES = "AFK_TIMER_MINUTES"
DB_KEY_STT_ENABLED = "STT_ENABLED"
DB_KEY_STT_ENGINE = "STT_ENGINE"
DB_KEY_VOSK_MODEL_PATH = "VOSK_MODEL_PATH"
DB_KEY_GARMIN_AUTO_JOIN_ENABLED = "GARMIN_AUTO_JOIN_ENABLED"
DB_KEY_GARMIN_AUTO_JOIN_CHANNELS = "GARMIN_AUTO_JOIN_CHANNELS"
DB_KEY_GARMIN_RECORD_SECONDS = "GARMIN_RECORD_SECONDS"
DB_KEY_GARMIN_MAX_RECORDING_DURATION = "GARMIN_MAX_RECORDING_DURATION"
DB_KEY_GARMIN_STT_OUTPUT_ENABLED = "GARMIN_STT_OUTPUT_ENABLED"
DB_KEY_GARMIN_DEFAULT_SAVE_DURATION = "GARMIN_DEFAULT_SAVE_DURATION"
DB_KEY_LOG_LEVEL = "LOG_LEVEL"
DB_KEY_DISCORD_LOG_LEVEL = "DISCORD_LOG_LEVEL"

# Original hardcoded default values (pre-database settings)
DEFAULT_LOG_CHANNEL_ID = 1266773678306230374
DEFAULT_BOT_AUDIT_ID = 1373288909542264852
DEFAULT_TECHSUPPORT_CHANNEL_ID = 1139952610883928134
DEFAULT_HIDDEN_CHANNELS_LIST = [1255930025463644232, 1233872680680296499, 374159356717039620]

# --- Global Settings Variables (with initial hardcoded defaults) ---
TESTING = False
HIDDEN_CHANNELS = []
LOG_CHANNEL_ID = 0
BOT_AUDIT_ID = 0
TECHSUPPORT_CHANNEL_ID = 0
AFK_CHANNEL_ID = 0
PURGE_OLDER_THAN_DAYS = 7
JOIN_MESSAGE_TIMER_ENABLED = True
JOIN_MESSAGE_TIMER_MINUTES = 7
AFK_TIMER_MINUTES = 10
STT_ENABLED = False
STT_ENGINE = 'google'
VOSK_MODEL_PATH = ''
GARMIN_AUTO_JOIN_ENABLED = False
GARMIN_AUTO_JOIN_CHANNELS = []
GARMIN_RECORD_SECONDS = 600
GARMIN_MAX_RECORDING_DURATION = 3600
GARMIN_STT_OUTPUT_ENABLED = True
GARMIN_DEFAULT_SAVE_DURATION = 30 * 60 # Default to 30 minutes
LOG_LEVEL = 'INFO'
DISCORD_LOG_LEVEL = 'INFO'

def load_all_settings():
    global AFK_TIMER_MINUTES, TESTING, BOT_AUDIT_ID, GARMIN_AUTO_JOIN_CHANNELS, GARMIN_AUTO_JOIN_ENABLED, GARMIN_MAX_RECORDING_DURATION, GARMIN_RECORD_SECONDS, GARMIN_STT_OUTPUT_ENABLED, HIDDEN_CHANNELS, JOIN_MESSAGE_TIMER_ENABLED, JOIN_MESSAGE_TIMER_MINUTES, LOG_CHANNEL_ID, LOG_LEVEL, DISCORD_LOG_LEVEL, PURGE_OLDER_THAN_DAYS, STT_ENABLED, STT_ENGINE, TECHSUPPORT_CHANNEL_ID, VOSK_MODEL_PATH, AFK_CHANNEL_ID, GARMIN_DEFAULT_SAVE_DURATION
    
    logger.info("Loading dynamic settings...")

    # --- APP_TESTING_MODE ---
    app_testing_mode_db_val = get_setting(DB_KEY_APP_TESTING_MODE)
    if app_testing_mode_db_val is None:
        app_testing_mode_db_val = os.environ.get('APP_TESTING_MODE', 'False')
        save_setting(DB_KEY_APP_TESTING_MODE, app_testing_mode_db_val)
    TESTING = app_testing_mode_db_val.lower() == 'true'

    # --- HIDDEN_CHANNELS ---
    hc_str_db_val = get_setting(DB_KEY_HIDDEN_CHANNELS)
    if hc_str_db_val is None:
        hc_str_db_val = ','.join(map(str, DEFAULT_HIDDEN_CHANNELS_LIST))
        save_setting(DB_KEY_HIDDEN_CHANNELS, hc_str_db_val)
    if hc_str_db_val and hc_str_db_val.strip():
        try:
            HIDDEN_CHANNELS = [int(x.strip()) for x in hc_str_db_val.split(',') if x.strip()]
        except ValueError:
            HIDDEN_CHANNELS = list(DEFAULT_HIDDEN_CHANNELS_LIST)
    else:
        HIDDEN_CHANNELS = []

    # --- Channel IDs ---
    def load_channel_id(key: str, default_id: int):
        id_str_db_val = get_setting(key)
        if id_str_db_val is None:
            id_str_db_val = str(default_id)
            save_setting(key, id_str_db_val)
        if id_str_db_val and id_str_db_val.lower() != 'none':
            try:
                return int(id_str_db_val)
            except (ValueError, TypeError):
                return default_id
        return None

    LOG_CHANNEL_ID = load_channel_id(DB_KEY_LOG_CHANNEL_ID, DEFAULT_LOG_CHANNEL_ID)
    BOT_AUDIT_ID = load_channel_id(DB_KEY_BOT_AUDIT_ID, DEFAULT_BOT_AUDIT_ID)
    TECHSUPPORT_CHANNEL_ID = load_channel_id(DB_KEY_TECHSUPPORT_CHANNEL_ID, DEFAULT_TECHSUPPORT_CHANNEL_ID)
    AFK_CHANNEL_ID = load_channel_id(DB_KEY_AFK_CHANNEL_ID, 0)

    # --- PURGE_OLDER_THAN_DAYS ---
    purge_days_str = get_setting(DB_KEY_PURGE_OLDER_THAN_DAYS, '7')
    PURGE_OLDER_THAN_DAYS = int(purge_days_str)

    # --- JOIN_MESSAGE_TIMER_ENABLED ---
    join_timer_enabled_str = get_setting(DB_KEY_JOIN_MESSAGE_TIMER_ENABLED, 'true')
    JOIN_MESSAGE_TIMER_ENABLED = join_timer_enabled_str.lower() == 'true'

    # --- JOIN_MESSAGE_TIMER_MINUTES ---
    join_timer_minutes_str = get_setting(DB_KEY_JOIN_MESSAGE_TIMER_MINUTES, '7')
    JOIN_MESSAGE_TIMER_MINUTES = int(join_timer_minutes_str)

    # --- AFK_TIMER_MINUTES ---
    afk_timer_minutes_str = get_setting(DB_KEY_AFK_TIMER_MINUTES, '10')
    AFK_TIMER_MINUTES = int(afk_timer_minutes_str)

    # --- Garmin Recorder Settings ---
    stt_enabled_str = get_setting(DB_KEY_STT_ENABLED, os.environ.get('STT_ENABLED', 'true'))
    STT_ENABLED = stt_enabled_str.lower() == 'true'

    STT_ENGINE = get_setting(DB_KEY_STT_ENGINE, os.environ.get('STT_ENGINE', 'google'))
    VOSK_MODEL_PATH = get_setting(DB_KEY_VOSK_MODEL_PATH, os.environ.get('VOSK_MODEL_PATH', 'assets/models'))

    garmin_auto_join_enabled_str = get_setting(DB_KEY_GARMIN_AUTO_JOIN_ENABLED, os.environ.get('GARMIN_AUTO_JOIN_ENABLED', 'false'))
    GARMIN_AUTO_JOIN_ENABLED = garmin_auto_join_enabled_str.lower() == 'true'

    garmin_auto_join_channels_str = get_setting(DB_KEY_GARMIN_AUTO_JOIN_CHANNELS, os.environ.get('GARMIN_AUTO_JOIN_CHANNELS', ''))
    if garmin_auto_join_channels_str:
        GARMIN_AUTO_JOIN_CHANNELS = [int(x.strip()) for x in garmin_auto_join_channels_str.split(',') if x.strip()]
    else:
        GARMIN_AUTO_JOIN_CHANNELS = []

    GARMIN_RECORD_SECONDS = int(get_setting(DB_KEY_GARMIN_RECORD_SECONDS, os.environ.get('GARMIN_RECORD_SECONDS', '600')))
    GARMIN_MAX_RECORDING_DURATION = int(get_setting(DB_KEY_GARMIN_MAX_RECORDING_DURATION, os.environ.get('GARMIN_MAX_RECORDING_DURATION', '3600')))
    
    # --- Garmin STT Output Settings ---
    garmin_stt_output_enabled_str = get_setting(DB_KEY_GARMIN_STT_OUTPUT_ENABLED, os.environ.get('GARMIN_STT_OUTPUT_ENABLED', 'true'))
    GARMIN_STT_OUTPUT_ENABLED = garmin_stt_output_enabled_str.lower() == 'true'
    
    # --- Garmin Default Save Duration ---
    garmin_default_save_duration_str = get_setting(DB_KEY_GARMIN_DEFAULT_SAVE_DURATION, os.environ.get('GARMIN_DEFAULT_SAVE_DURATION', '30'))
    GARMIN_DEFAULT_SAVE_DURATION = int(garmin_default_save_duration_str)
    
    # --- General Settings ---
    LOG_LEVEL = get_setting(DB_KEY_LOG_LEVEL, os.environ.get('LOG_LEVEL', 'INFO'))
    logging.getLogger().setLevel(LOG_LEVEL.upper())

    # --- Discord Log Level Settings ---
    DISCORD_LOG_LEVEL = get_setting(DB_KEY_DISCORD_LOG_LEVEL, os.environ.get('DISCORD_LOG_LEVEL', 'INFO'))
    
    # Set separate log level for all discord.* packages
    discord_logger = logging.getLogger('discord')
    discord_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    # Also set specific loggers for discord submodules to ensure they respect the setting
    discord_http_logger = logging.getLogger('discord.http')
    discord_http_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    discord_gateway_logger = logging.getLogger('discord.gateway')
    discord_gateway_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    discord_voice_logger = logging.getLogger('discord.voice_client')
    discord_voice_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    logger.info(f"Set discord.* loggers to {DISCORD_LOG_LEVEL.upper()}")
    
    logger.info("Finished loading dynamic settings.")

def apply_discord_log_level():
    """Apply the current Discord log level setting to all discord loggers."""
    global DISCORD_LOG_LEVEL
    
    # Set separate log level for all discord.* packages
    discord_logger = logging.getLogger('discord')
    discord_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    # Also set specific loggers for discord submodules to ensure they respect the setting
    discord_http_logger = logging.getLogger('discord.http')
    discord_http_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    discord_gateway_logger = logging.getLogger('discord.gateway')
    discord_gateway_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    discord_voice_logger = logging.getLogger('discord.voice_client')
    discord_voice_logger.setLevel(DISCORD_LOG_LEVEL.upper())
    
    logger.info(f"Applied discord.* loggers to {DISCORD_LOG_LEVEL.upper()}")

# --- Initial load ---
load_all_settings()
