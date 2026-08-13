import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import voice_stats


SCHEMA = """
CREATE TABLE user_voice_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    username TEXT,
    display_name_global TEXT,
    display_name_server TEXT,
    channel_id INTEGER,
    channel_name TEXT,
    event_type TEXT,
    timestamp TEXT
)
"""

# Databases created before display names were logged.
LEGACY_SCHEMA = """
CREATE TABLE user_voice_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    username TEXT,
    channel_id INTEGER,
    channel_name TEXT,
    event_type TEXT,
    timestamp TEXT
)
"""

CHANNEL_A = 100
CHANNEL_B = 200


class VoiceStatsTestCase(unittest.TestCase):
    """The event log only stores join/switch/leave, so every number on the
    dashboard depends on sessions being reconstructed correctly."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)
        self._next_id = 0

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def add_event(self, user_id, username, channel_id, channel_name, event_type, timestamp,
                  display_global=None, display_server=None):
        self._next_id += 1
        self.conn.execute(
            "INSERT INTO user_voice_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._next_id, user_id, username, display_global, display_server,
             channel_id, channel_name, event_type, f"2026-08-01T{timestamp}+00:00"),
        )
        self.conn.commit()

    def stats(self, afk_channel_id=0):
        return voice_stats.collect_statistics(
            self.db_path, afk_channel_id=afk_channel_id, period_key='all')

    def seconds_by_name(self, entries):
        return {entry['name']: entry['seconds'] for entry in entries}

    def test_join_leave_pair_counts_as_one_session(self):
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '11:00:00')

        result = self.stats()

        self.assertEqual(self.seconds_by_name(result['top_users']), {'alice': 3600})
        self.assertEqual(result['kpis']['total_sessions'], 1)

    def test_switch_splits_time_across_both_channels(self):
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '12:00:00')
        self.add_event(1, 'alice', CHANNEL_B, 'B', 'switch', '12:30:00')
        self.add_event(1, 'alice', CHANNEL_B, 'B', 'leave', '13:00:00')

        result = self.stats()

        self.assertEqual(self.seconds_by_name(result['top_channels']), {'A': 1800, 'B': 1800})
        self.assertEqual(self.seconds_by_name(result['top_users']), {'alice': 3600})

    def test_missed_leave_is_closed_by_the_next_join(self):
        self.add_event(1, 'bob', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'bob', CHANNEL_A, 'A', 'join', '11:00:00')
        self.add_event(1, 'bob', CHANNEL_A, 'A', 'leave', '12:00:00')

        result = self.stats()

        self.assertEqual(self.seconds_by_name(result['top_users']), {'bob': 7200})
        self.assertEqual(result['kpis']['total_sessions'], 2)

    def test_leave_without_join_is_ignored(self):
        self.add_event(1, 'carol', CHANNEL_A, 'A', 'leave', '10:00:00')

        result = self.stats()

        self.assertFalse(result['has_data'])
        self.assertEqual(result['top_users'], [])

    def test_renamed_channel_stays_a_single_entry(self):
        self.add_event(1, 'dave', CHANNEL_A, 'Old Name', 'join', '09:00:00')
        self.add_event(1, 'dave', CHANNEL_A, 'New Name', 'leave', '09:30:00')

        channels = self.stats()['top_channels']

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]['name'], 'New Name')
        self.assertEqual(channels[0]['seconds'], 1800)

    def test_afk_time_and_share_are_reported(self):
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '12:00:00')
        self.add_event(1, 'alice', CHANNEL_B, 'AFK', 'switch', '12:30:00')
        self.add_event(1, 'alice', CHANNEL_B, 'AFK', 'leave', '13:00:00')

        result = self.stats(afk_channel_id=CHANNEL_B)

        self.assertEqual(len(result['top_afk']), 1)
        self.assertEqual(result['top_afk'][0]['name'], 'alice')
        self.assertEqual(result['top_afk'][0]['seconds'], 1800)
        self.assertEqual(result['top_afk'][0]['share'], 50.0)
        self.assertEqual(result['kpis']['afk_share'], 50.0)

    def test_runaway_session_is_capped(self):
        # A leave that never arrived must not produce a month-long session.
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '00:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '23:59:59')
        self.conn.execute(
            "UPDATE user_voice_events SET timestamp = '2026-09-30T00:00:00+00:00' WHERE id = 2")
        self.conn.commit()

        result = self.stats()

        self.assertEqual(result['top_users'][0]['seconds'], voice_stats.MAX_SESSION_SECONDS)

    def test_empty_database_reports_no_data(self):
        result = self.stats()

        self.assertFalse(result['has_data'])
        self.assertEqual(result['kpis']['total_sessions'], 0)
        self.assertEqual(result['kpis']['busiest_day'], '-')
        self.assertEqual(len(result['by_hour']), 24)
        self.assertEqual(len(result['by_weekday']), 7)

    def test_unknown_period_falls_back_to_default(self):
        result = voice_stats.collect_statistics(self.db_path, period_key='not-a-period')

        self.assertEqual(result['period']['key'], voice_stats.DEFAULT_PERIOD)

    def test_newest_display_names_win_over_blank_history(self):
        # Older rows predate the columns; the newest non-empty value must survive.
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '11:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '12:00:00',
                       display_global='Alice!', display_server='Mod Alice')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '13:00:00')

        entry = self.stats()['top_users'][0]

        self.assertEqual(entry['display_global'], 'Alice!')
        self.assertEqual(entry['display_server'], 'Mod Alice')

    def test_display_names_default_to_empty(self):
        self.add_event(1, 'bob', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'bob', CHANNEL_A, 'A', 'leave', '11:00:00')

        entry = self.stats()['top_users'][0]

        self.assertEqual(entry['display_global'], '')
        self.assertEqual(entry['display_server'], '')

    def test_database_without_display_columns_still_works(self):
        # A database that has not been migrated yet must not break the dashboard.
        handle, legacy_path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.executescript(LEGACY_SCHEMA)
            legacy.execute(
                "INSERT INTO user_voice_events VALUES (1, 1, 'alice', ?, 'A', 'join', ?)",
                (CHANNEL_A, '2026-08-01T10:00:00+00:00'))
            legacy.execute(
                "INSERT INTO user_voice_events VALUES (2, 1, 'alice', ?, 'A', 'leave', ?)",
                (CHANNEL_A, '2026-08-01T11:00:00+00:00'))
            legacy.commit()

            result = voice_stats.collect_statistics(legacy_path, period_key='all')

            self.assertEqual(result['top_users'][0]['seconds'], 3600)
            self.assertEqual(result['top_users'][0]['display_global'], '')
        finally:
            legacy.close()
            os.unlink(legacy_path)

    def custom(self, from_date=None, to_date=None):
        return voice_stats.collect_statistics(
            self.db_path, period_key='custom', custom_from=from_date, custom_to=to_date)

    def test_custom_range_covers_the_whole_selected_days(self):
        # 10:00-11:00 on the 1st, and again on the 3rd.
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '11:00:00')
        self.conn.execute("UPDATE user_voice_events SET timestamp = '2026-08-03T10:00:00+00:00' WHERE id = 1")
        self.conn.execute("UPDATE user_voice_events SET timestamp = '2026-08-03T11:00:00+00:00' WHERE id = 2")
        self.conn.commit()

        inside = self.custom('2026-08-03', '2026-08-03')
        outside = self.custom('2026-08-04', '2026-08-05')

        self.assertEqual(inside['kpis']['total_sessions'], 1)
        self.assertEqual(outside['kpis']['total_sessions'], 0)

    def test_custom_range_reversed_dates_give_the_same_window(self):
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '11:00:00')

        forward = self.custom('2026-08-01', '2026-08-05')
        reversed_ = self.custom('2026-08-05', '2026-08-01')

        self.assertEqual(forward['period']['start'], reversed_['period']['start'])
        self.assertEqual(forward['period']['end'], reversed_['period']['end'])
        self.assertEqual(forward['kpis']['total_sessions'], reversed_['kpis']['total_sessions'])

    def test_custom_range_end_names_the_last_day_included(self):
        result = self.custom('2026-08-01', '2026-08-05')

        # The window is exclusive internally; the label must not leak the 6th.
        self.assertEqual(result['period']['end'], '2026-08-05')

    def test_custom_range_without_dates_falls_back(self):
        self.assertEqual(self.custom()['period']['key'], voice_stats.DEFAULT_PERIOD)
        self.assertEqual(self.custom('nonsense', '')['period']['key'], voice_stats.DEFAULT_PERIOD)

    def test_length_distribution_buckets_sessions(self):
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '10:02:00')   # < 5m
        self.add_event(2, 'bob', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(2, 'bob', CHANNEL_A, 'A', 'leave', '12:00:00')     # 1-3h

        buckets = {b['label']: b['value'] for b in self.stats()['length_distribution']}

        self.assertEqual(buckets['< 5m'], 1)
        self.assertEqual(buckets['1–3h'], 1)
        self.assertEqual(buckets['> 6h'], 0)
        self.assertEqual(sum(buckets.values()), 2)

    def test_longest_sessions_are_ranked_and_carry_context(self):
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(1, 'alice', CHANNEL_A, 'A', 'leave', '11:00:00')
        self.add_event(2, 'bob', CHANNEL_B, 'B', 'join', '10:00:00')
        self.add_event(2, 'bob', CHANNEL_B, 'B', 'leave', '13:00:00')

        longest = self.stats()['longest_sessions']

        self.assertEqual(longest[0]['name'], 'bob')
        self.assertEqual(longest[0]['seconds'], 3 * 3600)
        self.assertEqual(longest[0]['channel'], 'B')
        self.assertIn('2026', longest[0]['when'])
        self.assertEqual(longest[1]['name'], 'alice')

    def test_longest_sessions_exclude_capped_sessions(self):
        # A join whose leave never arrived would otherwise top the ranking.
        self.add_event(1, 'ghost', CHANNEL_A, 'A', 'join', '00:00:00')
        self.add_event(2, 'alice', CHANNEL_A, 'A', 'join', '10:00:00')
        self.add_event(2, 'alice', CHANNEL_A, 'A', 'leave', '11:00:00')
        self.conn.execute(
            "UPDATE user_voice_events SET timestamp = '2026-09-30T00:00:00+00:00' WHERE id = 1")
        self.conn.commit()

        longest = self.stats()['longest_sessions']

        self.assertEqual([entry['name'] for entry in longest], ['alice'])

    def test_format_duration(self):
        self.assertEqual(voice_stats.format_duration(45), '45s')
        self.assertEqual(voice_stats.format_duration(90), '1m')
        self.assertEqual(voice_stats.format_duration(3660), '1h 1m')
        self.assertEqual(voice_stats.format_duration(90000), '1d 1h')
        self.assertEqual(voice_stats.format_duration(-5), '0s')


if __name__ == '__main__':
    unittest.main()
