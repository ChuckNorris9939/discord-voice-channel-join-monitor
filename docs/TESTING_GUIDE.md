# Manual Testing Guide for Thread Inactivity Monitoring

This guide outlines the steps to manually test the thread inactivity monitoring and auto-closure feature of the Discord bot.

## Prerequisites

1.  **Bot Running:** Ensure the bot is running with the latest code containing the inactivity monitoring feature.
2.  **Configuration:**
    *   `TECHSUPPORT_CHANNEL_ID` in `main.py` must be correctly set to the ID of a **Forum Channel** in your test server.
    *   The `CLOSED_TAG_NAME` (default: "🔒 CLOSED") must exist as an available tag in the configured tech support forum channel.
3.  **Shortened Time Deltas (IMPORTANT for timely testing):**
    *   Temporarily modify the `datetime.timedelta` values in the `check_inactive_threads_task` function in `main.py`.
    *   Change `days=2` (for the 48h warning) to `minutes=X` (e.g., `minutes=4`).
    *   Change `days=1` (for the 24h reminder and final closure) to `minutes=Y` (e.g., `minutes=2`).
    *   **Example (quick test cycle):**
        *   Warning after 4 minutes: `now - last_activity_dt > datetime.timedelta(minutes=4)`
        *   Reminder 2 minutes after warning: `now - warning_sent_dt > datetime.timedelta(minutes=2)`
        *   Closure 2 minutes after reminder: `now - reminder_sent_dt > datetime.timedelta(minutes=2)`
    *   **Remember to revert these `timedelta` changes to their original values (e.g., `days=2`, `days=1`) after testing is complete.**
4.  **Bot Permissions:** Ensure the bot has permissions to:
    *   Read messages in the forum.
    *   Send messages in threads.
    *   Manage threads (lock, archive, apply tags).
    *   Fetch users (to mention them).

## Test Scenarios

### 1. New Thread Creation & Initial Scan

*   **Action:**
    1.  Stop the bot if it's running.
    2.  Start the bot.
    3.  In the designated tech support forum channel, create a new thread (post).
    4.  Send a message in that new thread.
    5.  Restart the bot.
*   **Expected Outcome:**
    *   Check the bot's logs. You should see messages from `scan_existing_threads` indicating it's processing this new thread.
    *   The thread should be added to the `inactive_threads` database table. (Verification might require a debug command to view DB contents or direct DB inspection if possible).
    *   The `last_activity_timestamp`, `op_user_id`, and `last_message_user_id` should be correctly recorded.

### 2. Activity Reset

*   **Action:**
    1.  Have a thread that is already being tracked (e.g., from Scenario 1, or after a warning/reminder has been sent).
    2.  Send a new message in this thread.
*   **Expected Outcome:**
    *   Check bot logs for the `on_message` handler processing activity for this thread.
    *   The `last_activity_timestamp` for this thread in the `inactive_threads` table should be updated to the timestamp of the new message.
    *   If `warning_sent_timestamp` or `reminder_sent_timestamp` were previously set for this thread, they should be reset to `NULL` in the database.

### 3. Full Inactivity Cycle (with shortened times)

*   **Action:**
    1.  Create a new thread in the tech support forum.
    2.  Send one message in it from the "OP" user.
    3.  Optionally, have another user send a message to test mentions for both.
    4.  Do not interact with the thread further.
    5.  Wait for the (shortened) "48h warning" period (e.g., 4 minutes).
*   **Expected (Stage 1 - Warning):**
    *   The bot posts a warning message in the thread (e.g., "Dieser Thread ist seit 48 Stunden inaktiv...").
    *   The message should mention the OP and the last user who posted (if different from OP).
    *   The `warning_sent_timestamp` for the thread in the `inactive_threads` table should be set to the current time.
*   **Action (Continued):**
    1.  Wait for the (shortened) "24h reminder" period after the warning (e.g., 2 minutes).
*   **Expected (Stage 2 - Reminder):**
    *   The bot posts a reminder message in the thread (e.g., "Erinnerung: Dieser Thread ist weiterhin inaktiv...").
    *   The `reminder_sent_timestamp` for the thread in the `inactive_threads` table should be set.
*   **Action (Continued):**
    1.  Wait for the (shortened) "final closure" period after the reminder (e.g., 2 minutes).
*   **Expected (Stage 3 - Closure):**
    *   The bot sends a final message indicating closure due to inactivity.
    *   The bot calls `close_support_thread`, which should:
        *   Lock the thread.
        *   Archive the thread.
        *   Apply the `CLOSED_TAG_NAME` (e.g., "🔒 CLOSED") to the thread.
    *   The thread's record should be removed from the `inactive_threads` database table.

### 4. Activity Interrupting Warning/Reminder

*   **Action:**
    1.  Create a new thread and send a message.
    2.  Wait for the (shortened) "48h warning" period. Confirm the warning message is posted.
    3.  **Before** the (shortened) "24h reminder" period is up, send a new message in the thread.
*   **Expected Outcome:**
    *   The `last_activity_timestamp` is updated.
    *   The `warning_sent_timestamp` (and `reminder_sent_timestamp`, if applicable) for the thread should be reset to `NULL` in the database.
    *   The thread should **not** receive a reminder message based on the *previous* warning.
    *   The inactivity cycle effectively restarts from this new activity. The thread would need to go through the full (shortened) 48h warning period again before another warning is issued.

### 5. Manual Closure by Tag

*   **Action:**
    1.  Create a new thread and send a message. Let it be picked up by `scan_existing_threads` or the `on_message` handler so it's tracked.
    2.  Manually apply the `CLOSED_TAG_NAME` (e.g., "🔒 CLOSED") to this thread in Discord.
    3.  Send another message in the thread *after* the tag has been applied.
*   **Expected Outcome:**
    *   **`on_message` behavior:** The bot's `on_message` handler should see the closed tag and log that it's ignoring activity in an already closed thread. The `last_activity_timestamp` in the database should *not* be updated for this new message.
    *   **`check_inactive_threads_task` behavior:** When the daily task (`check_inactive_threads_task`) next runs, it should identify that this thread has the closed tag.
    *   The task should then remove the thread's record from the `inactive_threads` database table.

### 6. Thread Deletion

*   **Action:**
    1.  Create a new thread and send a message. Ensure it's being tracked in the `inactive_threads` table.
    2.  Manually delete the thread directly from Discord (not just closing it, but fully deleting the post).
*   **Expected Outcome:**
    *   When the `check_inactive_threads_task` next runs, it will attempt to fetch this thread from Discord.
    *   It should receive a "Not Found" (404) error or similar from Discord.
    *   The task should log that the thread was not found and then call `remove_thread_activity` to delete its record from the `inactive_threads` database table.

---
**Remember to revert the `timedelta` changes in `main.py` after completing your tests.**
---

## Verifying Bot Version Announcement

1.  **Console/File Logs:** After starting the bot, check the standard output or log file. You should see a log line similar to:
    `YYYY-MM-DD HH:MM:SS [INFO] discord_bot: Bot version: 1.9 starting up...`
    (Replace "1.9" with the actual value of `BOT_VERSION` if it differs).
2.  **Discord Channels:** Check the channels specified by `LOG_CHANNEL_ID` and `BOT_AUDIT_ID` in your Discord server. You should see a message similar to:
    `✅ Bot version 1.9 gestartet und einsatzbereit.`
    (Replace "1.9" with the actual value of `BOT_VERSION` if it differs).

---

## Testing Configurable Database Path

1.  **Initial Startup:**
    *   Before starting the bot for the first time with this change, ensure there is no `config` directory and no `user_log.db` in the root or `data` directory.
    *   Start the bot.
    *   **Expected:**
        *   A `config` directory is created in the bot's root directory.
        *   The `user_log.db` file is created inside the `data` directory.
        *   The bot operates normally, logging to the console that it's using/created the DB in the `config` directory (check for logs like "Ensured configuration directory 'config' exists." and database connection messages referencing the path).
2.  **Data Persistence:**
    *   Perform some actions that would write to the database (e.g., trigger user join/leave for `user_joins`, let a support thread go through an inactivity cycle for `inactive_threads`, change a setting on the `/settings` page for `bot_settings`).
    *   Stop the bot.
    *   Restart the bot.
    *   **Expected:** The bot should load the previous data from `/data/user_log.db`. Verify this by checking logs, the `/settings` page (settings should persist), or other relevant bot behavior (e.g., `viewlogs` command).
3.  **Existing Database (Migration Test - Manual):**
    *   If you have an existing `user_log.db` in the root directory from a previous version:
        *   Manually create a `data` directory.
        *   Manually move the old `user_log.db` into the `data` directory.
        *   Start the new version of the bot.
        *   **Expected:** The bot should pick up and use the existing database from `/data/user_log.db` seamlessly. All previous data should be intact and usable.

---

## Testing Restart Button Functionality

1.  **Prerequisite:** Ensure the bot is run by a process manager (e.g., Docker with restart policy, systemd service, or a simple `while true; do python main.py; done` shell loop) that will automatically restart it if the process exits.
2.  **Trigger Restart:**
    *   Navigate to the `/settings` page on the bot's Flask web UI.
    *   Click the "Restart Bot" button.
    *   Confirm the action in the browser's confirmation dialog.
3.  **Observe Behavior:**
    *   **Expected (Bot Logs):** The bot's console logs should show messages related to `graceful_shutdown` being initiated (e.g., "Restart command received via web UI.", "Scheduling graceful_shutdown...", "Shutdown-Signal empfangen...", "Stoppe msg_purge_task...", "Bot wird gestoppt...", "Bot-Verbindung erfolgreich geschlossen.", "Graceful shutdown abgeschlossen.").
    *   **Expected (Process):** The bot process should terminate cleanly.
    *   **Expected (Process Manager):** The configured process manager should detect the termination and restart the `main.py` script.
    *   **Expected (Bot Logs on Restart):** You should see the normal startup logs, including the version announcement and "Bot gestartet" messages.
4.  **Caution Note:** If no process manager is active, clicking 'Restart Bot' will simply stop the bot. It will not restart on its own. This is expected behavior.

---

## Testing `APP_TESTING_MODE` Display on Home Page

1.  **Initial State:**
    *   Start the bot.
    *   Navigate to the bot's home page (`/`) in a web browser.
    *   **Expected:** The "Application Testing Mode" status (ON/OFF) should be displayed. This should reflect the current effective setting (from environment variable `APP_TESTING_MODE` initially, or from the database if previously set).
2.  **Change via `/settings`:**
    *   Navigate to `/settings`.
    *   Change the "App Testing Mode" (e.g., from OFF to ON, or ON to OFF). Click "Save Settings".
    *   Navigate back to the home page (`/`).
    *   **Expected:** The displayed "Application Testing Mode" status should update to reflect the change made in the settings.
3.  **Verify Bot Behavior (if applicable):**
    *   If `APP_TESTING_MODE` influences other behaviors (like logging channels):
        *   If `TESTING` is set to `True`, check that bot log messages (e.g., from `send_log_message`) are directed to the `TESTING_CHANNEL_ID` (if this channel ID is configured).
        *   If `TESTING` is set to `False`, check that bot log messages are directed to the production `LOG_CHANNEL_ID` and `BOT_AUDIT_ID`.
        *   This can be verified by checking the bot's console logs for messages indicating which channel IDs are being used after the setting change and by observing messages in the respective Discord channels.

---
