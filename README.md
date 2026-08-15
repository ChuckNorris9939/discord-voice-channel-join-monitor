# discord-voice-channel-join-monitor

**Version 2.1.0**

A Discord bot that logs voice channel activity, manages support forum threads, and exposes a web dashboard with voice statistics. Built on py-cord with an embedded Flask server.

```bash
git clone --branch master --single-branch https://github.com/ChuckNorris9939/discord-voice-channel-join-monitor.git
docker compose up -d --build
```

## Features

- **Voice activity logging** — every join, leave and channel switch is written to SQLite, including username, global display name and server nickname
- **Voice statistics dashboard** — time spent per user and per channel, AFK ranking, activity by hour and weekday, session length distribution, longest sessions; selectable period including a custom date range
- **Filterable join log** — Excel-style per-column filters with search, multi-select and a date range
- **Support forum management** — inactive threads get a warning after 48 h, a reminder after 72 h and are closed automatically after 96 h
- **Voice recording** with optional real-time speech-to-text (Google Cloud Speech or offline Vosk) — see the DAVE note below
- **Automatic cleanup** of old recordings and of old messages in the log channels
- **Role-based web UI access** via Authentik groups

## Requirements

- Docker and Docker Compose (recommended), or Python 3.10+ with `ffmpeg` installed
- A Discord bot token

## Installation

```bash
cp env.example .env      # then fill in DISCORD_TOKEN
docker compose up -d --build
```

Without Docker:

```bash
pip install -r requirements.txt
python main.py
```

`data/` holds the database, logs and recordings and must be mounted as a volume so it survives a container rebuild.

### Configuration

`DISCORD_TOKEN` is required. Everything else has a sensible default and can be changed at runtime under `/settings`.

**Values saved in the web UI take precedence over `.env`.** Treat `.env` as the starting point for a fresh database — once a setting exists in the `bot_settings` table, the `.env` entry no longer has any effect. `DISCORD_TOKEN` and `PORT` are the exception; they are read from the environment only.

Common variables:

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | — | required |
| `PORT` | `8080` | web server port |
| `LOG_LEVEL` | `INFO` | application log level |
| `APP_TESTING_MODE` | `false` | redirects all bot messages to the testing channel |
| `STT_ENGINE` | `google` | `google` or `vosk` |
| `TEMP_DAVE_FIX` | `false` | join voice channels without recording, see below |

## Web interface

Reachable on the configured port, by default `http://localhost:8080`:

| Path | Contents |
|---|---|
| `/` | dashboard with status, uptime and version |
| `/statistics` | voice statistics, `?period=7d\|30d\|90d\|365d\|all\|custom` |
| `/view_join_logs` | full join/leave log with column filters |
| `/garmin_recordings` | recordings, grouped by session |
| `/settings` | all runtime settings |
| `/status` | health check as JSON |

### Access control

The app has no login of its own. Authentik sits in front as a forward-auth proxy and passes the logged-in user's groups in `X-authentik-groups`. Under **Settings → Access Control** five areas can each be restricted to a list of groups: general access, join logs, settings, bot control and recordings.

Holding **any one** of the listed groups grants access — Discord roles are flat, so an admin group has to be listed everywhere it should reach, general access included. An empty field leaves that area open, which is the default, so deploying cannot lock anyone out.

## Slash commands

| Command | Purpose |
|---|---|
| `/users` | list everyone currently in a visible voice channel |
| `/viewlogs` | last join events (administrators only) |
| `/close` | close a support thread |
| `/delete` | delete messages in the current channel |
| `/garmin-start`, `/garmin-stop`, `/garmin-save` | control recording |
| `/garmin-health`, `/garmin-autojoin` | recording status and auto-join |

A `!!` prefix is configured for text commands as well.

## Known limitation: voice recording and DAVE

Discord made its DAVE end-to-end encryption mandatory for voice on 2 March 2026. py-cord 2.8 implements DAVE for sending only — **voice reception is still unimplemented** ([py-cord #3139](https://github.com/Pycord-Development/pycord/issues/3139)), so `start_recording()` crashes and the bot drops out of the channel.

Until that is fixed, set `TEMP_DAVE_FIX = true` (Settings → Garmin). The bot then joins voice channels and stays connected, but records nothing. Voice **logging** and all statistics are unaffected — they do not require the bot to be in the channel.

## Health checks

```bash
curl http://localhost:8080/status     # JSON status
python health_check.py                # external monitor with automatic restart
docker compose -f docker-compose.health.yml up -d
```

Details in [docs/HEALTH_CHECK.md](docs/HEALTH_CHECK.md).

## Tests

```bash
python -m unittest tests.test_main tests.test_voice_stats
```

`main.py` requires Python 3.10+, so any test importing it has to run inside the container:

```bash
docker run --rm -v "$(pwd)":/app -w /app --entrypoint python dc_voice_monitor \
  -m unittest tests.test_main tests.test_voice_stats
```

## Further documentation

- [CLAUDE.md](CLAUDE.md) — architecture and the pitfalls worth knowing before changing anything
- [docs/AUDIO_CLEANUP_IMPLEMENTATION.md](docs/AUDIO_CLEANUP_IMPLEMENTATION.md) — retention logic for recordings
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) — manual test procedure for thread inactivity monitoring
- [docs/HEALTH_CHECK.md](docs/HEALTH_CHECK.md) — health monitoring setup

Never commit `.env` — it contains the bot token.
