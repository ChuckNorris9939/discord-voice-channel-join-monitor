# discord-voice-channel-join-monitor
**Version 1.14**

Sends messages when user joins to a voice channel

clone command: `git clone --branch master --single-branch https://github.com/ChuckNorris9939/discord-voice-channel-join-monitor.git`
build command: `docker build . -t dc_voice_monitor`

## Installation

### Dependencies
Install the required Python packages using pip:
```bash
pip install -r requirements.txt
```

### Environment Configuration
The bot supports loading environment variables from a `.env` file for easier configuration management.

1. **Copy the example file:**
   ```bash
   cp env.example .env
   ```

2. **Edit the `.env` file** with your actual values:
   ```bash
   # Required: Your Discord bot token
   DISCORD_TOKEN=your_actual_discord_bot_token_here
   
   # Optional: Other configuration settings
   APP_TESTING_MODE=false
   LOG_LEVEL=INFO
   PORT=8080
   ```

3. **Important:** Never commit your `.env` file to version control as it contains sensitive information like your Discord token.

The bot will automatically load the `.env` file when it starts. If the file is not found or there's an error loading it, the bot will fall back to system environment variables.

### Health Check Port
The bot runs a small web server for health checks on port 8080 by default. If this port is already in use on your system (you might see an `OSError: [Errno 98] Address already in use`), you can specify a different port by setting the `PORT` environment variable before running the bot:
```bash
export PORT=8081
# Then run your bot
```
Or include it in your `.env` file if you are using one.

### Testing Mode
You can enable a testing mode by setting the `APP_TESTING_MODE` environment variable to `true`.
This setting can also be managed via the `/settings` page in the web UI.
```bash
export APP_TESTING_MODE=true
```
When testing mode is active:
*   A log message "TESTING MODE ENABLED: Overriding LOG_CHANNEL_ID and BOT_AUDIT_ID to 1376227809474908253" will be printed at startup.
*   The `LOG_CHANNEL_ID` and `BOT_AUDIT_ID` will both be set to `1376227809474908253`, redirecting critical logs and audit messages to this specific channel.

## User Join Logging

This bot includes a feature to log user voice channel join events.
When a user joins a visible voice channel, the following information is recorded in an SQLite database file named `user_log.db`:
- The SQLite database (`user_log.db`) is stored within a `config/` directory, which is automatically created in the bot's root folder if it doesn't exist.
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

### Web Interface for Logs
A web interface is available to browse all user join logs stored in the database and manage bot settings. You can access it at the following paths on the server where the bot is running:

*   `/view_join_logs`: Browse user join logs.
*   `/settings`: View and modify bot settings.

For example, if your bot is accessible at `http://localhost:8080`, the interfaces would be at `http://localhost:8080/view_join_logs` and `http://localhost:8080/settings`. The port is the same one used by the Flask server for health checks (default 8080, configurable via the `PORT` environment variable).

#### Bot Settings Page (`/settings`)
The `/settings` page allows for dynamic configuration of several bot parameters, including:
- App Testing Mode
- Hidden Channel IDs
- Log Channel ID
- Bot Audit ID
- Tech Support Channel ID

Changes saved here are stored in the database and loaded by the bot. Some settings might also be influenced by environment variables as initial defaults.

##### Restart Bot Functionality
The `/settings` page includes a "Restart Bot" button. Clicking this button will trigger a graceful shutdown of the bot. **Important:** For the bot to restart automatically after shutdown, you must be running it using a process manager (like Docker with a restart policy, systemd, pm2, or a simple `while true` loop in a shell script) that handles automatic restarts after process termination. Without a process manager, the bot will simply stop.
