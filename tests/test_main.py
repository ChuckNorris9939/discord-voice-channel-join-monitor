import unittest
from unittest.mock import MagicMock, AsyncMock, patch, call
import asyncio
import sqlite3
import tempfile
import datetime
import os

# Temporarily add the app directory to sys.path to allow main import
# This is often needed if tests are run from a different directory context
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Conditional import for main to allow testing without running the bot
# We will primarily be testing functions, so we might not need the full bot object from main.
# If main.py has code that runs on import (outside __main__ check), we need to be careful.
# For this test, we assume main.py can be imported and we can access its functions.
# We will be patching main.sqlite3.connect to use an in-memory DB for tests.
import main

# Each test gets its own throwaway database file. A plain ":memory:" database
# cannot be used here: main.py opens a new connection per operation, and every
# ":memory:" connection is a separate empty database, so nothing it writes would
# ever be visible to the next connection or to the assertions below.
TEST_DB_NAME = None  # set per test in setUp

class TestMain(unittest.TestCase):

    def setUp(self):
        """Setup for each test. Creates a throwaway SQLite database on disk."""
        handle, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        
        # The database file is brand new per test, so there is nothing to clean up.

        # Patch 'sqlite3.connect' in the 'main' module's scope
        # All calls to sqlite3.connect within main.py will now use our in-memory database
        # Keep a reference to the real connect: main.sqlite3 is the very same
        # module object as sqlite3 here, so patching it would make the
        # replacement call itself and recurse until the stack blows.
        real_connect = sqlite3.connect
        self.mock_sqlite_connect = patch(
            'main.sqlite3.connect',
            side_effect=lambda *args, **kwargs: real_connect(self.db_path))
        self.mock_sqlite_connect.start()

        # main.py creates its tables here; without this every test that reads a
        # table fails with "no such table".
        main.init_user_log_db()

        # Mock main.logger to prevent log output during tests and allow assertions on calls
        # start() returns the mock; the patcher itself has no .info to assert on.
        self.logger_patcher = patch('main.logger', MagicMock())
        self.mock_logger = self.logger_patcher.start()

        # Mock main.send_log_message as it's called in on_voice_state_update and not relevant to DB logging
        self.mock_send_log_message = patch('main.send_log_message', AsyncMock())
        self.mock_send_log_message.start()
        
        # Mock main.bot as it's used in on_voice_state_update and viewlogs
        # We need a loop for asyncio.run
        self.mock_bot = MagicMock()
        # asyncio.run() closes the loop it uses, so a shared/global loop would be
        # closed for every test after the first async one.
        self.mock_bot.loop = asyncio.new_event_loop()
        # For viewlogs, if it's a hybrid command, it might be registered on the bot.
        # We'll mock the callback directly if possible, or mock the bot's tree.
        # For on_voice_state_update, bot.get_guild is used.
        self.mock_bot.get_guild = MagicMock()
        main.bot = self.mock_bot # Replace the actual bot instance in main with our mock

        # Config lives in config_loader now, not as module globals on main.
        import config_loader as cfg
        self.cfg = cfg
        cfg.JOIN_LOGS_ID = 987654321
        cfg.BOT_LOGS_ID = 987654322
        cfg.HIDDEN_CHANNELS = [111, 222]
        cfg.TESTING = False
        cfg.GARMIN_AUTO_JOIN_ENABLED = False
        cfg.GARMIN_AUTO_JOIN_CHANNELS = []
        cfg.JOIN_MESSAGE_TIMER_ENABLED = False
        main.DISCORD_SERVER_ID = 123456789
        main.USERS = []
        main.garmin_manager = None

    def tearDown(self):
        """Tear down after each test."""
        self.mock_sqlite_connect.stop()
        self.logger_patcher.stop()
        self.mock_send_log_message.stop()
        self.conn.close()
        self.mock_bot.loop.close()
        os.unlink(self.db_path)

    def test_init_user_log_db(self):
        """Test the database initialization function."""
        main.init_user_log_db() # This will use the patched connect

        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in self.cursor.fetchall()}
        self.assertIn('user_voice_events', tables)
        self.assertIn('inactive_threads', tables)
        self.assertIn('bot_settings', tables)
        # The legacy table is dropped on init and must not come back.
        self.assertNotIn('user_joins', tables)

        self.cursor.execute("PRAGMA table_info(user_voice_events)")
        columns = {row[1]: row[2] for row in self.cursor.fetchall()}
        expected_columns = {
            "id": "INTEGER",
            "user_id": "INTEGER",
            "username": "TEXT",
            "display_name_global": "TEXT",
            "display_name_server": "TEXT",
            "channel_id": "INTEGER",
            "channel_name": "TEXT",
            "event_type": "TEXT",
            "timestamp": "TEXT"
        }
        self.assertEqual(columns, expected_columns, "Table 'user_voice_events' schema does not match expected.")

        # Verify 'inactive_threads' table schema
        self.cursor.execute("PRAGMA table_info(inactive_threads)")
        inactive_columns = {row[1]: row[2] for row in self.cursor.fetchall()}
        expected_inactive_columns = {
            "thread_id": "INTEGER",
            "guild_id": "INTEGER",
            "last_activity_timestamp": "TEXT",
            "warning_sent_timestamp": "TEXT",
            "reminder_sent_timestamp": "TEXT",
            "op_user_id": "INTEGER",
            "last_message_user_id": "INTEGER"
        }
        self.assertEqual(inactive_columns, expected_inactive_columns, "Table 'inactive_threads' schema does not match expected.")

    def test_init_user_log_db_is_idempotent(self):
        """Running init twice must not lose data or duplicate columns."""
        main.log_voice_event(1, 'alice', 2, 'General', 'join')
        main.init_user_log_db()

        self.cursor.execute("SELECT COUNT(*) FROM user_voice_events")
        self.assertEqual(self.cursor.fetchone()[0], 1, "Re-running init dropped existing rows.")










    # --- Tests for inactive_threads DB helper functions ---

    def test_add_or_update_thread_activity_insert(self):
        """Test inserting a new thread activity record."""
        main.init_user_log_db() # Ensure schema
        thread_id = 12345
        guild_id = 98765
        last_activity_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        op_user_id = 111
        last_message_user_id = 222

        main.add_or_update_thread_activity(thread_id, guild_id, last_activity_ts, op_user_id, last_message_user_id)

        self.cursor.execute("SELECT * FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        record = self.cursor.fetchone()
        self.assertIsNotNone(record)
        self.assertEqual(record[0], thread_id)
        self.assertEqual(record[1], guild_id)
        self.assertEqual(record[2], last_activity_ts)
        self.assertIsNone(record[3]) # warning_sent_timestamp
        self.assertIsNone(record[4]) # reminder_sent_timestamp
        self.assertEqual(record[5], op_user_id)
        self.assertEqual(record[6], last_message_user_id)
        self.mock_logger.info.assert_called_with(f"New activity recorded for thread {thread_id}: Inserted into inactive_threads.")

    def test_add_or_update_thread_activity_update(self):
        """Test updating an existing thread activity record."""
        main.init_user_log_db()
        thread_id = 12345
        guild_id = 98765
        initial_op_user_id = 111
        initial_last_msg_user_id = 222
        initial_activity_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        warning_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).isoformat()
        
        # Insert initial record with a warning
        self.cursor.execute("""
            INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id, warning_sent_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (thread_id, guild_id, initial_activity_ts, initial_op_user_id, initial_last_msg_user_id, warning_ts))
        self.conn.commit()

        new_activity_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_op_user_id = 112 
        new_last_message_user_id = 223

        main.add_or_update_thread_activity(thread_id, guild_id, new_activity_ts, new_op_user_id, new_last_message_user_id)

        self.cursor.execute("SELECT * FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        record = self.cursor.fetchone()
        self.assertIsNotNone(record)
        self.assertEqual(record[2], new_activity_ts) # last_activity updated
        self.assertIsNone(record[3]) # warning_sent_timestamp should be NULL
        self.assertIsNone(record[4]) # reminder_sent_timestamp should be NULL
        self.assertEqual(record[5], new_op_user_id) # op_user_id updated
        self.assertEqual(record[6], new_last_message_user_id) # last_message_user_id updated
        self.mock_logger.info.assert_called_with(f"Activity updated for thread {thread_id}: Updated existing record in inactive_threads, reset warning/reminder.")

    def test_get_thread_activity_exists(self):
        """Test retrieving an existing thread's activity."""
        main.init_user_log_db()
        thread_id = 54321
        guild_id = 123
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.cursor.execute("INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id) VALUES (?, ?, ?, ?, ?)",
                       (thread_id, guild_id, ts, 1, 2))
        self.conn.commit()

        record = main.get_thread_activity(thread_id)
        self.assertIsNotNone(record)
        self.assertEqual(record['thread_id'], thread_id)
        self.assertEqual(record['last_activity_timestamp'], ts)

    def test_get_thread_activity_not_exists(self):
        """Test retrieving a non-existent thread's activity."""
        main.init_user_log_db()
        record = main.get_thread_activity(99999)
        self.assertIsNone(record)

    def test_get_all_thread_activities_empty(self):
        """Test getting all activities when DB is empty."""
        main.init_user_log_db()
        records = main.get_all_thread_activities()
        self.assertEqual(len(records), 0)

    def test_get_all_thread_activities_multiple(self):
        """Test getting all activities with multiple records."""
        main.init_user_log_db()
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.cursor.execute("INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id) VALUES (1, 10, ?, 1,1)", (ts,))
        self.cursor.execute("INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id) VALUES (2, 10, ?, 2,2)", (ts,))
        self.conn.commit()

        records = main.get_all_thread_activities()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['thread_id'], 1)
        self.assertEqual(records[1]['thread_id'], 2)

    def test_remove_thread_activity(self):
        """Test removing a thread activity record."""
        main.init_user_log_db()
        thread_id = 777
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.cursor.execute("INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id) VALUES (?, 10, ?, 1, 1)", (thread_id, ts))
        self.conn.commit()

        main.remove_thread_activity(thread_id)
        
        self.cursor.execute("SELECT * FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        self.assertIsNone(self.cursor.fetchone())
        self.mock_logger.info.assert_called_with(f"Removed thread activity record for thread_id: {thread_id}")

    def test_remove_thread_activity_not_exists(self):
        """Test removing a non-existent thread activity record."""
        main.init_user_log_db()
        thread_id = 888
        main.remove_thread_activity(thread_id) # Should not error
        self.mock_logger.warning.assert_called_with(f"Attempted to remove thread activity for thread_id: {thread_id}, but no record was found.")


    def test_update_thread_warning_sent(self):
        """Test updating warning_sent_timestamp."""
        main.init_user_log_db()
        thread_id = 666
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.cursor.execute("INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id) VALUES (?, 10, ?, 1,1)", (thread_id, ts))
        self.conn.commit()

        warning_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        main.update_thread_warning_sent(thread_id, warning_ts)

        self.cursor.execute("SELECT warning_sent_timestamp FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        self.assertEqual(self.cursor.fetchone()[0], warning_ts)
        self.mock_logger.info.assert_called_with(f"Updated warning_sent_timestamp for thread_id: {thread_id} to {warning_ts}")

    def test_update_thread_reminder_sent(self):
        """Test updating reminder_sent_timestamp."""
        main.init_user_log_db()
        thread_id = 555
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.cursor.execute("INSERT INTO inactive_threads (thread_id, guild_id, last_activity_timestamp, op_user_id, last_message_user_id) VALUES (?, 10, ?, 1,1)", (thread_id, ts))
        self.conn.commit()

        reminder_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        main.update_thread_reminder_sent(thread_id, reminder_ts)

        self.cursor.execute("SELECT reminder_sent_timestamp FROM inactive_threads WHERE thread_id = ?", (thread_id,))
        self.assertEqual(self.cursor.fetchone()[0], reminder_ts)
        self.mock_logger.info.assert_called_with(f"Updated reminder_sent_timestamp for thread_id: {thread_id} to {reminder_ts}")


if __name__ == '__main__':
    unittest.main()
