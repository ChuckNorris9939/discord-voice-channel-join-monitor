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
