# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Discord bot that monitors voice channel joins/leaves, manages support forum threads, records voice conversations with optional Speech-to-Text, and exposes a Flask web dashboard. The project is primarily English-language (Discord messages, log output, and UI text are in English).

## Commands

### Running the bot

```bash
# always use .venv enviroment
source  .venv/bin/activate
# Local development
pip install -r requirements.txt
cp env.example .env  # then fill in DISCORD_TOKEN and other settings
python main.py

# Docker (recommended)
docker-compose up -d

# Docker (with build)
docker-compose up --build -d

# Docker with health monitoring
docker-compose -f docker-compose.health.yml up -d

# Build Docker image
docker build . -t dc_voice_monitor
# or
./build.sh
```

### Testing

```bash
python -m unittest tests/test_main.py
python -m unittest tests.test_voice_stats
python -m unittest tests/test_garmin_voice.py
python -m unittest tests/test_web_interface.py
python -m unittest tests/test_aligned_recording.py
python -m unittest tests/test_aligned_implementation.py

# Audio mixing performance tests
python test_mixing_performance.py
python test_mixing_performance_simple.py
```

### Health checks

```bash
python health_check.py
./health_check.sh
curl http://localhost:8080/status
```

## Architecture

### Core files

- **[main.py](main.py)** (~3200 lines) — Discord bot + embedded Flask web server. Contains all Discord event handlers, slash commands, background tasks, SQLite schema + queries, and all Flask routes. This is the primary file.
- **[garmin_voice.py](garmin_voice.py)** (~1700 lines) — Voice recording system. Implements `LiveSTTMP3Sink`, a custom Discord audio sink that captures 48kHz stereo PCM, does silence compression, per-user track isolation, audio mixing, and optional real-time STT.
- **[voice_stats.py](voice_stats.py)** — Analytics for the `/statistics` dashboard. The log only stores discrete join/switch/leave events, so this reconstructs *sessions* by pairing them per user (a `switch` closes the running session and opens a new one for the destination channel). Rankings group by `user_id`/`channel_id` and display the newest name, because both get renamed over time. Sessions are capped at `MAX_SESSION_SECONDS` so a missed `leave` cannot dominate a ranking.
- **[config_loader.py](config_loader.py)** — Centralized settings with three-layer priority: environment variables (`.env`) → SQLite `bot_settings` table → hardcoded defaults.
- **[audio_cleanup_service.py](audio_cleanup_service.py)** — Deletes old recordings from `data/garmin-output/` and `data/aligned-recordings/` based on configurable retention hours. Runs every 6 hours as a background task.
- **[health_check.py](health_check.py)** — External health monitor that checks container status, `/status` endpoint, and Discord connectivity, with automatic restart on failure.

### Data flow

1. Discord voice events → `on_voice_state_update` in `main.py` → writes to `user_voice_events` SQLite table
2. Voice recording → `garmin_voice.py` `LiveSTTMP3Sink` → MP3 files in `data/garmin-output/` (optionally `data/aligned-recordings/`)
3. Settings changes via `/settings` web UI → written immediately to `bot_settings` SQLite table → read by `config_loader.py` on next access
4. All persistent data lives under `data/` (must be mounted as a volume in Docker)

### Database (SQLite at `data/user_log.db`)

Three tables, auto-created on startup:
- `user_voice_events` — voice join/leave log (user_id, username, channel, event_type, timestamp)
- `inactive_threads` — support forum thread tracking with escalation timestamps (warning → reminder → auto-close)
- `bot_settings` — key/value dynamic configuration store

### Background tasks (run inside the bot)

- **`check_inactive_threads_task`** (every 24h) — 3-stage escalation for inactive support threads: 48h warning → 72h reminder → 96h auto-close
- **`msg_purge_task`** (every 24h) — bulk-deletes messages older than configured threshold from JOIN_LOGS and BOT_LOGS channels (capped at 14 days due to Discord API limit)
- **`periodic_cleanup_task`** (every 6h) — triggers `AudioCleanupService`

### Web server (Flask on port 8080)

Key routes: `/` dashboard, `/view_join_logs`, `/statistics` (voice analytics, `?period=7d|30d|90d|365d|all`), `/garmin_recordings`, `/settings`, `/restart_bot`, `/status` (health JSON), `/cleanup/run`, `/garmin/start|stop|save|autojoin|stt_output`. Templates are in `templates/`.

Templates have no external dependencies — no CDN scripts or fonts. Charts on `/statistics` are hand-rolled CSS bars and inline SVG so the dashboard works without internet access.

### STT engines

Configured via `STT_ENGINE` env var:
- `google` — Google Cloud Speech API (requires credentials)
- `vosk` — Offline model; model files must be downloaded and placed at `VOSK_MODEL_PATH` (default: `data/assets/models/`)

## Configuration

Copy `env.example` to `.env`. Required: `DISCORD_TOKEN`. All settings can also be changed at runtime via the `/settings` web UI, which persists to the `bot_settings` database table. `config_loader.py` merges all sources on read with env vars taking highest priority.

Key env vars: `LOG_LEVEL`, `PORT` (default 8080), `STT_ENGINE`, `GARMIN_RECORDING_DURATION`, `GARMIN_SILENCE_COMPRESSION`, `APP_TESTING_MODE`.

## Docker notes

- Container name: `discord-tanga-bot`; host port 8083 → container 8080
- `data/` must be volume-mounted to persist the database, logs, and recordings across restarts
- System dependency `ffmpeg` is installed in the Dockerfile (required by pydub for MP3 output)
- Dockerfile health check pings `/status` every 30s
