# discord-voice-channel-join-monitor
Sends messages when user joins to a voice channel

clone command: `git clone --branch master --single-branch https://github.com/ChuckNorris9939/discord-voice-channel-join-monitor.git`
build command: `docker build . -t dc_voice_monitor`

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
