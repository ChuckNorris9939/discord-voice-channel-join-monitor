# discord-voice-channel-join-monitor
Sends messages when user joins to a voice channel

clone command: `git clone --branch master --single-branch https://github.com/ChuckNorris9939/discord-voice-channel-join-monitor.git`
build command: `docker build . -t dc_voice_monitor`

## Installation

### Dependencies
Install the required Python packages using pip:
```bash
pip install -r requirements.txt
```

### Health Check Port
The bot runs a small web server for health checks on port 8080 by default. If this port is already in use on your system (you might see an `OSError: [Errno 98] Address already in use`), you can specify a different port by setting the `PORT` environment variable before running the bot:
```bash
export PORT=8081
# Then run your bot
```
Or include it in your `.env` file if you are using one.

### Testing Mode
You can enable a testing mode by setting the `APP_TESTING_MODE` environment variable to `true`.
```bash
export APP_TESTING_MODE=true
```
When testing mode is active:
*   A log message "TESTING MODE ENABLED: Overriding LOG_CHANNEL_ID and BOT_AUDIT_ID to 1376227809474908253" will be printed at startup.
*   The `LOG_CHANNEL_ID` and `BOT_AUDIT_ID` will both be set to `1376227809474908253`, redirecting critical logs and audit messages to this specific channel.

## User Join Logging

This bot includes a feature to log user voice channel join events.
When a user joins a visible voice channel, the following information is recorded in an SQLite database file named `user_log.db`:
- User ID
- Username
- Voice Channel ID
- Voice Channel Name
- Timestamp (UTC, ISO format)

### `viewlogs` Command

To view the latest join events, administrators can use the `viewlogs` command.

- **Purpose**: Displays the last 10 user join events recorded in the database.
- **Access**: Restricted to users with Administrator permissions on the server.
- **Usage**:
    - As a slash command: `/viewlogs`
    - As a traditional command: `!!viewlogs` (if the `!!` prefix is configured)

The output will be sent as an ephemeral message, visible only to the administrator who invoked the command.
