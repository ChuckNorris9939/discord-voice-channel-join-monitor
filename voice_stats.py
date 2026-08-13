"""Voice activity statistics derived from the user_voice_events log.

The event log only stores discrete join/switch/leave events. Everything here is
built on top of *sessions*, which are reconstructed by pairing those events per
user, so the rankings show actual time spent in voice rather than raw event
counts.
"""

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("discord_bot")

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None

# Events are stored in UTC, but the server is German - hour-of-day and weekday
# breakdowns are only meaningful in local time.
DEFAULT_TIMEZONE = "Europe/Berlin"

# A session longer than this is assumed to be a missed 'leave' (bot restart,
# dropped gateway event) and gets capped, so one gap cannot dominate a ranking.
MAX_SESSION_SECONDS = 24 * 3600

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

PERIODS = [
    ("7d", "Last 7 days", 7),
    ("30d", "Last 30 days", 30),
    ("90d", "Last 90 days", 90),
    ("365d", "Last 12 months", 365),
    ("all", "All time", None),
    ("custom", "Custom range…", None),
]
DEFAULT_PERIOD = "30d"
CUSTOM_PERIOD = "custom"

TOP_N = 10

# Buckets for the session length distribution, as (upper bound in seconds, label).
# The final bucket has no upper bound.
LENGTH_BUCKETS = [
    (5 * 60, "< 5m"),
    (30 * 60, "5–30m"),
    (60 * 60, "30m–1h"),
    (3 * 3600, "1–3h"),
    (6 * 3600, "3–6h"),
    (None, "> 6h"),
]


def format_duration(seconds):
    """Render a second count as a compact human readable duration."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _resolve_timezone(name=DEFAULT_TIMEZONE):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name), name
        except Exception:
            logger.warning(f"Timezone '{name}' unavailable, falling back to UTC")
    return timezone.utc, "UTC"


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value):
    """Parse a YYYY-MM-DD string from the date picker, or None if unusable."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d")
    except (AttributeError, ValueError):
        return None


def _day_bounds(day, tz, end_of_day=False):
    """Turn a calendar day into an instant, interpreted in the display timezone
    so picking a day means that whole local day and not a shifted UTC window."""
    if end_of_day:
        day += timedelta(days=1)  # exclusive upper bound
    return day.replace(tzinfo=tz).astimezone(timezone.utc)


def _resolve_period(period_key, custom_from=None, custom_to=None, tz=timezone.utc):
    """Return (key, label, window_start, window_end) for the requested period."""
    now = datetime.now(timezone.utc)

    if period_key == CUSTOM_PERIOD:
        first = _parse_date(custom_from)
        last = _parse_date(custom_to)
        if first or last:
            # Order the days before turning them into bounds, so a reversed
            # selection covers exactly the same range as the correct order.
            if first and last and first > last:
                first, last = last, first
            start = _day_bounds(first, tz) if first else None
            end = min(_day_bounds(last, tz, end_of_day=True) if last else now, now)
            return CUSTOM_PERIOD, "Custom range", start, end
        # Custom selected without usable dates - fall back rather than error.
        return _resolve_period(DEFAULT_PERIOD, tz=tz)

    for key, label, days in PERIODS:
        if key == period_key and key != CUSTOM_PERIOD:
            start = None if days is None else now - timedelta(days=days)
            return key, label, start, now
    return _resolve_period(DEFAULT_PERIOD, tz=tz)


def _load_events(db_path, window_start):
    """Load events, including a lookback so sessions crossing the window start
    are still reconstructed from their real join event."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # The display-name columns were added to the log later. Select NULL in
        # their place when reading a database that has not been migrated yet,
        # so the dashboard still renders instead of erroring out.
        available = {row[1] for row in cursor.execute("PRAGMA table_info(user_voice_events)")}
        optional = [name if name in available else f"NULL AS {name}"
                    for name in ("display_name_global", "display_name_server")]

        query = (
            f"SELECT user_id, username, {optional[0]}, {optional[1]}, "
            "channel_id, channel_name, event_type, timestamp "
            "FROM user_voice_events"
        )
        params = []
        if window_start is not None:
            # Timestamps are ISO 8601 with a fixed +00:00 offset, so lexicographic
            # comparison matches chronological order.
            lookback = window_start - timedelta(seconds=MAX_SESSION_SECONDS)
            query += " WHERE timestamp >= ?"
            params.append(lookback.isoformat())
        query += " ORDER BY timestamp"

        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        conn.close()


def _build_sessions(rows, window_start, window_end):
    """Pair join/switch/leave events into sessions clipped to the window.

    A 'switch' records the destination channel, so it closes the running
    session and immediately opens a new one.
    """
    per_user = defaultdict(list)
    for row in rows:
        moment = _parse_timestamp(row["timestamp"])
        if moment is not None:
            per_user[row["user_id"]].append((moment, row))

    sessions = []
    for events in per_user.values():
        events.sort(key=lambda item: item[0])
        open_row = None
        open_start = None

        for moment, row in events:
            if open_row is not None:
                sessions.append(_make_session(open_row, open_start, moment))
                open_row = open_start = None
            if row["event_type"] in ("join", "switch"):
                open_row, open_start = row, moment

        if open_row is not None:
            # Still in the channel, or the matching leave was never logged.
            sessions.append(_make_session(open_row, open_start, window_end))

    clipped = []
    for session in sessions:
        start = session["start"] if window_start is None else max(session["start"], window_start)
        end = min(session["end"], window_end)
        if end <= start and not (session["start"] == session["end"] and start == end):
            continue
        session["start"] = start
        session["end"] = end
        session["duration"] = max(0.0, (end - start).total_seconds())
        clipped.append(session)
    return clipped


def _make_session(row, start, end):
    duration = (end - start).total_seconds()
    capped = duration > MAX_SESSION_SECONDS
    if capped:
        duration = MAX_SESSION_SECONDS
        end = start + timedelta(seconds=MAX_SESSION_SECONDS)
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "channel_id": row["channel_id"],
        "channel_name": row["channel_name"],
        "start": start,
        "end": end,
        "duration": max(0.0, duration),
        # A capped session means the closing event was never logged, so its
        # length is an artefact and must not appear in a "longest" ranking.
        "capped": capped,
    }


def _latest_names(rows):
    """Map ids to their most recent name. Users and channels get renamed, so
    grouping must key on the id while display uses the current name.

    Only non-empty values are kept, which matters for the display names: they
    are absent on every row logged before the columns existed, and a blank must
    not overwrite a name a newer event already told us about.
    """
    users = {}
    channels = {}
    display_global = {}
    display_server = {}
    for row in rows:  # rows arrive ordered by timestamp, so later wins
        if row["username"]:
            users[row["user_id"]] = row["username"]
        if row["channel_name"]:
            channels[row["channel_id"]] = row["channel_name"]
        if row["display_name_global"]:
            display_global[row["user_id"]] = row["display_name_global"]
        if row["display_name_server"]:
            display_server[row["user_id"]] = row["display_name_server"]
    return users, channels, display_global, display_server


def _spread_over_buckets(session, tz, by_hour, by_weekday, by_day):
    """Distribute a session's duration across the local hour/day buckets it spans."""
    start = session["start"].astimezone(tz)
    end = session["end"].astimezone(tz)
    cursor = start
    while cursor < end:
        next_hour = (cursor + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        chunk_end = min(next_hour, end)
        seconds = (chunk_end - cursor).total_seconds()
        by_hour[cursor.hour] += seconds
        by_weekday[cursor.weekday()] += seconds
        by_day[cursor.date()] += seconds
        cursor = chunk_end


def _rank(totals, sessions_count, names, limit=TOP_N):
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [
        {
            "id": str(key),
            "name": names.get(key, str(key)),
            "seconds": int(seconds),
            "duration": format_duration(seconds),
            "sessions": sessions_count.get(key, 0),
        }
        for key, seconds in ranked
    ]


def collect_statistics(db_path, afk_channel_id=0, period_key=DEFAULT_PERIOD,
                       timezone_name=DEFAULT_TIMEZONE, custom_from=None, custom_to=None):
    """Build the full statistics payload for the dashboard."""
    tz, tz_label = _resolve_timezone(timezone_name)
    period_key, period_label, window_start, window_end = _resolve_period(
        period_key, custom_from, custom_to, tz)

    rows = _load_events(db_path, window_start)
    user_names, channel_names, display_global, display_server = _latest_names(rows)
    effective_start = window_start or (
        _parse_timestamp(rows[0]["timestamp"]) if rows else window_end
    )
    sessions = _build_sessions(rows, window_start, window_end)

    user_seconds = defaultdict(float)
    user_sessions = defaultdict(int)
    channel_seconds = defaultdict(float)
    channel_sessions = defaultdict(int)
    afk_seconds = defaultdict(float)
    afk_sessions = defaultdict(int)
    by_hour = defaultdict(float)
    by_weekday = defaultdict(float)
    by_day = defaultdict(float)

    total_seconds = 0.0
    total_afk_seconds = 0.0

    for session in sessions:
        duration = session["duration"]
        user_id = session["user_id"]
        channel_id = session["channel_id"]

        user_seconds[user_id] += duration
        user_sessions[user_id] += 1
        channel_seconds[channel_id] += duration
        channel_sessions[channel_id] += 1
        total_seconds += duration

        if afk_channel_id and channel_id == afk_channel_id:
            afk_seconds[user_id] += duration
            afk_sessions[user_id] += 1
            total_afk_seconds += duration

        _spread_over_buckets(session, tz, by_hour, by_weekday, by_day)

    top_users = _rank(user_seconds, user_sessions, user_names)
    top_channels = _rank(channel_seconds, channel_sessions, channel_names)
    top_afk = _rank(afk_seconds, afk_sessions, user_names)

    # Attach the newest display names known for each user so the dashboard can
    # show them on hover. Empty until the user triggers a voice event that the
    # bot logs with the new columns in place.
    for entry in top_users + top_afk:
        user_id = int(entry["id"])
        entry["display_global"] = display_global.get(user_id, "")
        entry["display_server"] = display_server.get(user_id, "")

    # Show how much of each user's voice time was spent idling in AFK.
    for entry in top_afk:
        total_for_user = user_seconds.get(int(entry["id"]), 0)
        entry["share"] = round(entry["seconds"] / total_for_user * 100, 1) if total_for_user else 0.0

    hour_series = [
        {"label": f"{hour:02d}", "seconds": int(by_hour.get(hour, 0)),
         "duration": format_duration(by_hour.get(hour, 0))}
        for hour in range(24)
    ]
    weekday_series = [
        {"label": WEEKDAY_NAMES[day], "seconds": int(by_weekday.get(day, 0)),
         "duration": format_duration(by_weekday.get(day, 0))}
        for day in range(7)
    ]
    day_series = _build_day_series(by_day, effective_start, window_end, tz)

    busiest_day = max(by_day.items(), key=lambda item: item[1], default=None)
    busiest_hour = max(by_hour.items(), key=lambda item: item[1], default=None)
    busiest_weekday = max(by_weekday.items(), key=lambda item: item[1], default=None)

    return {
        "has_data": bool(sessions),
        "timezone": tz_label,
        "generated_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M"),
        "period": {
            "key": period_key,
            "label": period_label,
            "start": effective_start.astimezone(tz).strftime("%Y-%m-%d"),
            # The window end is exclusive, so step back to name the last day it
            # actually covers. For the rolling periods this is still today.
            "end": (window_end - timedelta(seconds=1)).astimezone(tz).strftime("%Y-%m-%d"),
            "options": [{"key": key, "label": label} for key, label, _ in PERIODS],
            # Echoed back so the date pickers keep showing what was submitted.
            "custom_from": custom_from or "",
            "custom_to": custom_to or "",
        },
        "kpis": {
            "total_time": format_duration(total_seconds),
            "total_sessions": len(sessions),
            "unique_users": len(user_seconds),
            "active_channels": len(channel_seconds),
            "avg_session": format_duration(total_seconds / len(sessions)) if sessions else "0m",
            "afk_share": round(total_afk_seconds / total_seconds * 100, 1) if total_seconds else 0.0,
            "afk_time": format_duration(total_afk_seconds),
            "busiest_day": busiest_day[0].strftime("%d.%m.%Y") if busiest_day else "-",
            "busiest_day_time": format_duration(busiest_day[1]) if busiest_day else "0m",
            "busiest_hour": f"{busiest_hour[0]:02d}:00" if busiest_hour else "-",
            "busiest_weekday": WEEKDAY_NAMES[busiest_weekday[0]] if busiest_weekday else "-",
        },
        "top_users": top_users,
        "top_channels": top_channels,
        "top_afk": top_afk,
        "by_hour": hour_series,
        "by_weekday": weekday_series,
        "by_day": day_series,
        "length_distribution": _length_distribution(sessions),
        "longest_sessions": _longest_sessions(sessions, user_names, channel_names, tz),
    }


def _length_distribution(sessions):
    """How session lengths are spread out - shows whether people drop in briefly
    or settle in for the evening."""
    counts = [0] * len(LENGTH_BUCKETS)
    for session in sessions:
        for index, (upper, _) in enumerate(LENGTH_BUCKETS):
            if upper is None or session["duration"] < upper:
                counts[index] += 1
                break

    total = sum(counts) or 1
    return [
        {
            "label": label,
            "value": count,
            "tooltip": f"{count} sessions ({count / total * 100:.0f}%)",
        }
        for count, (_, label) in zip(counts, LENGTH_BUCKETS)
    ]


def _longest_sessions(sessions, user_names, channel_names, tz, limit=TOP_N):
    """Single longest stays in voice. Sessions that hit the cap are left out:
    they are missing a leave event, not genuine marathons."""
    genuine = [session for session in sessions if not session.get("capped")]
    ranked = sorted(genuine, key=lambda item: item["duration"], reverse=True)[:limit]
    return [
        {
            "id": str(session["user_id"]),
            "name": user_names.get(session["user_id"], str(session["user_id"])),
            "seconds": int(session["duration"]),
            "duration": format_duration(session["duration"]),
            "sessions": 1,
            "channel": channel_names.get(session["channel_id"], str(session["channel_id"])),
            "when": session["start"].astimezone(tz).strftime("%d.%m.%Y %H:%M"),
        }
        for session in ranked
    ]


def _build_day_series(by_day, window_start, window_end, tz, max_points=120):
    """Continuous daily series so gaps show as gaps instead of being collapsed."""
    start_date = window_start.astimezone(tz).date()
    end_date = window_end.astimezone(tz).date()
    span = (end_date - start_date).days + 1
    if span <= 0:
        return []

    # Long ranges get bucketed so the chart stays readable.
    bucket_days = max(1, -(-span // max_points))
    series = []
    cursor = start_date
    while cursor <= end_date:
        bucket_end = min(cursor + timedelta(days=bucket_days - 1), end_date)
        seconds = sum(
            by_day.get(cursor + timedelta(days=offset), 0)
            for offset in range((bucket_end - cursor).days + 1)
        )
        series.append({
            "label": cursor.strftime("%d.%m.") if bucket_days == 1
                     else f"{cursor.strftime('%d.%m.')}-{bucket_end.strftime('%d.%m.')}",
            "seconds": int(seconds),
            "duration": format_duration(seconds),
        })
        cursor = bucket_end + timedelta(days=1)
    return series
