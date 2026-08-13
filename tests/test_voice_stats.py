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

    def add_event(self, user_id, username, channel_id, channel_name, event_type, timestamp):
        self._next_id += 1
        self.conn.execute(
            "INSERT INTO user_voice_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._next_id, user_id, username, channel_id, channel_name, event_type,
             f"2026-08-01T{timestamp}+00:00"),
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

    def test_format_duration(self):
        self.assertEqual(voice_stats.format_duration(45), '45s')
        self.assertEqual(voice_stats.format_duration(90), '1m')
        self.assertEqual(voice_stats.format_duration(3660), '1h 1m')
        self.assertEqual(voice_stats.format_duration(90000), '1d 1h')
        self.assertEqual(voice_stats.format_duration(-5), '0s')


if __name__ == '__main__':
    unittest.main()
