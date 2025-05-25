import unittest
from unittest.mock import MagicMock, AsyncMock, patch, call
import asyncio
import sqlite3
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

# Global variable to hold the test database connection for inspection
# This makes it easier for test methods to access the same in-memory db
# if we patch 'main.sqlite3.connect' to use this.
TEST_DB_NAME = ":memory:"

class TestMain(unittest.TestCase):

    def setUp(self):
        """Setup for each test. Creates an in-memory SQLite database."""
        self.conn = sqlite3.connect(TEST_DB_NAME)
        self.cursor = self.conn.cursor()
        
        # Ensure a clean state by trying to drop the table if it exists from a previous test run (optional)
        try:
            self.cursor.execute("DROP TABLE IF EXISTS user_joins")
            self.conn.commit()
        except sqlite3.Error:
            pass # Table might not exist yet, which is fine

        # Patch 'sqlite3.connect' in the 'main' module's scope
        # All calls to sqlite3.connect within main.py will now use our in-memory database
        self.mock_sqlite_connect = patch('main.sqlite3.connect', side_effect=lambda db_name: sqlite3.connect(TEST_DB_NAME))
        self.mock_sqlite_connect.start()

        # Mock main.logger to prevent log output during tests and allow assertions on calls
        self.mock_logger = patch('main.logger', MagicMock())
        self.mock_logger.start()

        # Mock main.send_log_message as it's called in on_voice_state_update and not relevant to DB logging
        self.mock_send_log_message = patch('main.send_log_message', AsyncMock())
        self.mock_send_log_message.start()
        
        # Mock main.bot as it's used in on_voice_state_update and viewlogs
        # We need a loop for asyncio.run
        self.mock_bot = MagicMock()
        self.mock_bot.loop = asyncio.get_event_loop()
        # For viewlogs, if it's a hybrid command, it might be registered on the bot.
        # We'll mock the callback directly if possible, or mock the bot's tree.
        # For on_voice_state_update, bot.get_guild is used.
        self.mock_bot.get_guild = MagicMock()
        main.bot = self.mock_bot # Replace the actual bot instance in main with our mock

        # Set necessary config values in main for tests
        main.DISCORD_SERVER_ID = 123456789 # Dummy server ID
        main.LOG_CHANNEL_ID = 987654321 # Dummy log channel ID
        main.BOT_AUDIT_ID = 987654322 # Dummy audit ID
        main.HIDDEN_CHANNELS = [111, 222] # Dummy hidden channels
        main.USERS = [] # Reset users list

    def tearDown(self):
        """Tear down after each test."""
        self.mock_sqlite_connect.stop()
        self.mock_logger.stop()
        self.mock_send_log_message.stop()
        self.conn.close()

    def test_init_user_log_db(self):
        """Test the database initialization function."""
        main.init_user_log_db() # This will use the patched connect

        # Verify table creation and schema
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_joins'")
        self.assertIsNotNone(self.cursor.fetchone(), "Table 'user_joins' was not created.")

        self.cursor.execute("PRAGMA table_info(user_joins)")
        columns = {row[1]: row[2] for row in self.cursor.fetchall()}
        
        expected_columns = {
            "id": "INTEGER",
            "user_id": "INTEGER",
            "username": "TEXT",
            "channel_id": "INTEGER",
            "channel_name": "TEXT",
            "timestamp": "TEXT"
        }
        self.assertEqual(columns, expected_columns, "Table 'user_joins' schema does not match expected.")
        self.mock_logger.info.assert_called_with("User log database initialized successfully (user_log.db and user_joins table).")

    async def run_on_voice_state_update(self, member, before, after):
        """Helper to run on_voice_state_update within the test's async context if needed"""
        # on_voice_state_update is an async function, so it should be awaited.
        # unittest.IsolatedAsyncioTestCase could be used for native async tests,
        # but for now, we can use asyncio.run or manage the loop if needed.
        # Since setUp provides a loop via mock_bot.loop, we can use it.
        await main.on_voice_state_update(member, before, after)

    def test_on_voice_state_update_user_joins_visible_channel(self):
        """Test logging when a user joins a visible channel."""
        main.init_user_log_db() # Initialize the in-memory DB schema

        mock_member = MagicMock(spec=main.discord.Member)
        mock_member.id = 101
        mock_member.name = "TestUser"
        mock_member.bot = False
        mock_member.guild = MagicMock(spec=main.discord.Guild)
        mock_member.guild.id = main.DISCORD_SERVER_ID

        mock_before_channel = None # User was not in a channel
        mock_after_channel = MagicMock(spec=main.discord.VoiceChannel) # Using VoiceChannel for clarity
        mock_after_channel.id = 301 # Visible channel ID
        mock_after_channel.name = "VisibleChannel"
        
        mock_before_state = MagicMock(spec=main.discord.VoiceState)
        mock_before_state.channel = mock_before_channel
        mock_before_state.self_stream = False
        mock_before_state.self_video = False


        mock_after_state = MagicMock(spec=main.discord.VoiceState)
        mock_after_state.channel = mock_after_channel
        mock_after_state.self_stream = False
        mock_after_state.self_video = False
        
        # Configure get_guild mock for the USER list update logic within on_voice_state_update
        self.mock_bot.get_guild.return_value = mock_member.guild

        asyncio.run(self.run_on_voice_state_update(mock_member, mock_before_state, mock_after_state))

        self.cursor.execute("SELECT user_id, username, channel_id, channel_name, timestamp FROM user_joins")
        record = self.cursor.fetchone()
        self.assertIsNotNone(record, "No record was inserted into user_joins.")
        self.assertEqual(record[0], mock_member.id)
        self.assertEqual(record[1], mock_member.name)
        self.assertEqual(record[2], mock_after_channel.id)
        self.assertEqual(record[3], mock_after_channel.name)
        self.assertIsNotNone(record[4]) # Timestamp exists
        # Verify timestamp is recent (optional, can be tricky due to timing)
        timestamp_dt = datetime.datetime.fromisoformat(record[4])
        self.assertTrue((datetime.datetime.now(datetime.timezone.utc) - timestamp_dt) < datetime.timedelta(seconds=5))
        self.assertIn(mock_member.name, main.USERS) # Check if user was added to the global USERS list

    def test_on_voice_state_update_bot_joins(self):
        """Test that bot joins are not logged."""
        main.init_user_log_db()

        mock_member = MagicMock(spec=main.discord.Member)
        mock_member.id = 202
        mock_member.name = "TestBot"
        mock_member.bot = True # This user is a bot
        mock_member.guild = MagicMock(spec=main.discord.Guild)
        mock_member.guild.id = main.DISCORD_SERVER_ID

        mock_after_channel = MagicMock(spec=main.discord.VoiceChannel)
        mock_after_channel.id = 301
        mock_after_channel.name = "VisibleChannel"
        
        mock_before_state = MagicMock(spec=main.discord.VoiceState, channel=None)
        mock_after_state = MagicMock(spec=main.discord.VoiceState, channel=mock_after_channel)
        
        asyncio.run(self.run_on_voice_state_update(mock_member, mock_before_state, mock_after_state))

        self.cursor.execute("SELECT COUNT(*) FROM user_joins")
        self.assertEqual(self.cursor.fetchone()[0], 0, "Bot join was logged, but it shouldn't have been.")

    def test_on_voice_state_update_user_leaves_visible_channel(self):
        """Test that no join log occurs when a user leaves."""
        main.init_user_log_db()
        # Add user to USERS list first to simulate they were there
        main.USERS.append("UserToLeave")


        mock_member = MagicMock(spec=main.discord.Member)
        mock_member.id = 303
        mock_member.name = "UserToLeave"
        mock_member.bot = False
        mock_member.guild = MagicMock(spec=main.discord.Guild)
        mock_member.guild.id = main.DISCORD_SERVER_ID
        
        # Mock member.voice.channel for the part of the code that checks if user is still in any visible VC
        mock_member.voice = MagicMock()
        mock_member.voice.channel = None # Simulate user not being in any VC after leaving

        mock_before_channel = MagicMock(spec=main.discord.VoiceChannel)
        mock_before_channel.id = 301 # Visible channel ID
        mock_before_channel.name = "VisibleChannel"
        
        mock_after_channel = None # User left
        
        mock_before_state = MagicMock(spec=main.discord.VoiceState, channel=mock_before_channel)
        mock_before_state.self_stream = False
        mock_before_state.self_video = False

        mock_after_state = MagicMock(spec=main.discord.VoiceState, channel=mock_after_channel)
        mock_after_state.self_stream = False
        mock_after_state.self_video = False

        # Configure get_guild mock for the USER list update logic within on_voice_state_update
        # This guild mock will return our mock_member when get_member is called.
        mock_guild_obj = MagicMock(spec=main.discord.Guild)
        mock_guild_obj.get_member.return_value = mock_member 
        self.mock_bot.get_guild.return_value = mock_guild_obj


        asyncio.run(self.run_on_voice_state_update(mock_member, mock_before_state, mock_after_state))

        self.cursor.execute("SELECT COUNT(*) FROM user_joins WHERE user_id = ?", (mock_member.id,))
        self.assertEqual(self.cursor.fetchone()[0], 0, "User leave was logged as a join.")
        self.assertNotIn(mock_member.name, main.USERS) # Check if user was removed from global USERS list

    def test_on_voice_state_update_user_moves_between_hidden_channels(self):
        """Test that no join log occurs for moves between hidden channels."""
        main.init_user_log_db()

        mock_member = MagicMock(spec=main.discord.Member)
        mock_member.id = 404
        mock_member.name = "HiddenUser"
        mock_member.bot = False
        mock_member.guild = MagicMock(spec=main.discord.Guild)
        mock_member.guild.id = main.DISCORD_SERVER_ID

        mock_before_channel = MagicMock(spec=main.discord.VoiceChannel)
        mock_before_channel.id = main.HIDDEN_CHANNELS[0] # Hidden channel
        mock_before_channel.name = "HiddenChannel1"
        
        mock_after_channel = MagicMock(spec=main.discord.VoiceChannel)
        mock_after_channel.id = main.HIDDEN_CHANNELS[1] # Another hidden channel
        mock_after_channel.name = "HiddenChannel2"

        mock_before_state = MagicMock(spec=main.discord.VoiceState, channel=mock_before_channel)
        mock_before_state.self_stream = False
        mock_before_state.self_video = False

        mock_after_state = MagicMock(spec=main.discord.VoiceState, channel=mock_after_channel)
        mock_after_state.self_stream = False
        mock_after_state.self_video = False
        
        asyncio.run(self.run_on_voice_state_update(mock_member, mock_before_state, mock_after_state))

        self.cursor.execute("SELECT COUNT(*) FROM user_joins WHERE user_id = ?", (mock_member.id,))
        self.assertEqual(self.cursor.fetchone()[0], 0, "User move between hidden channels was logged as a join.")

    def test_viewlogs_no_logs(self):
        """Test viewlogs command when there are no logs."""
        main.init_user_log_db() # Ensure table exists

        mock_ctx = MagicMock()
        # For hybrid commands, the send method might be on ctx.interaction.response or ctx.send
        # We'll mock both and check which one is called, or use AsyncMock for send.
        mock_ctx.send = AsyncMock()
        mock_ctx.interaction = None # Simulate non-interaction context for simplicity or mock interaction path

        # This is how you'd typically invoke a command's callback in discord.py tests
        # However, main.viewlogs is a HybridCommand. We need to simulate its invocation.
        # The callback is stored in `viewlogs.callback`.
        # The first argument to the callback is the 'self' or 'cog' instance, then context.
        # If viewlogs is not part of a Cog, 'self' would be the bot instance itself if it's bound,
        # or it might be implicitly handled. For a direct callback call, we might need to pass the bot.
        async def run_viewlogs():
             # The callback is main.viewlogs.callback. It expects (self, ctx, *args, **kwargs)
             # In this case, 'self' is the bot instance as it's not in a Cog.
            await main.viewlogs.callback(self.mock_bot, mock_ctx) 

        asyncio.run(run_viewlogs())
        
        mock_ctx.send.assert_called_once_with("Noch keine Join-Events in der Datenbank vorhanden.", ephemeral=True)

    def test_viewlogs_with_logs(self):
        """Test viewlogs command with some log data."""
        main.init_user_log_db()

        # Populate with some data
        log_entries = [
            (1, 501, 'UserA', 601, 'ChannelX', datetime.datetime.now(datetime.timezone.utc).isoformat()),
            (2, 502, 'UserB', 602, 'ChannelY', (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat())
        ]
        self.cursor.executemany("INSERT INTO user_joins (id, user_id, username, channel_id, channel_name, timestamp) VALUES (?, ?, ?, ?, ?, ?)", log_entries)
        self.conn.commit()

        mock_ctx = MagicMock()
        mock_ctx.send = AsyncMock()
        mock_ctx.interaction = None 
        mock_ctx.author = MagicMock() # For the error handler logging
        mock_ctx.channel = MagicMock()
        mock_ctx.channel.name = "test-channel"


        async def run_viewlogs():
            await main.viewlogs.callback(self.mock_bot, mock_ctx)

        asyncio.run(run_viewlogs())

        self.assertEqual(mock_ctx.send.call_count, 1)
        args, kwargs = mock_ctx.send.call_args
        response_message = args[0]
        
        self.assertTrue(kwargs.get('ephemeral'), "Message should be ephemeral.")
        self.assertIn("**Letzte 10 Benutzer-Join-Events:**", response_message)
        self.assertIn("UserA (ID: 501) trat Kanal bei: ChannelX (ID: 601)", response_message)
        self.assertIn("UserB (ID: 502) trat Kanal bei: ChannelY (ID: 602)", response_message)
        
        # Check timestamp formatting (example for one)
        # This relies on the timestamp in log_entries[0]
        dt_obj = datetime.datetime.fromisoformat(log_entries[0][5])
        formatted_timestamp = dt_obj.strftime('%Y-%m-%d %H:%M:%S UTC')
        self.assertIn(formatted_timestamp, response_message)

    def test_viewlogs_permission_error(self):
        """Test viewlogs command permission error."""
        mock_ctx = MagicMock()
        mock_ctx.send = AsyncMock()
        mock_ctx.author = MagicMock() # For the error handler logging
        mock_ctx.author.mention = "@TestUser"
        mock_ctx.channel = MagicMock()
        mock_ctx.channel.name = "test-channel"

        # Simulate missing permissions by raising the error the decorator would
        error = main.commands.MissingPermissions(['administrator'])

        async def run_viewlogs_error_handler():
            # Directly call the error handler attached to the command
            await main.viewlogs.error(self.mock_bot, mock_ctx, error)

        asyncio.run(run_viewlogs_error_handler())
        
        mock_ctx.send.assert_called_once_with("Du hast nicht die erforderlichen Berechtigungen, um diesen Befehl auszuführen.", ephemeral=True)


if __name__ == '__main__':
    unittest.main()
