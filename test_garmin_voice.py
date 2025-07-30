import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import wave
import time
from garmin_voice import GarminVoiceManager, voice_recv

class TestGarminVoice(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.bot = MagicMock()
        self.manager = GarminVoiceManager(self.bot)

    @patch('garmin_voice.subprocess.run')
    @patch('time.time')
    def test_save_recording_preserves_new_audio(self, mock_time, mock_subprocess_run):
        # Mock the timestamp
        mock_timestamp = 1234567890
        mock_time.return_value = mock_timestamp

        # Add some initial audio data to the buffer
        initial_audio = b'\x01\x02\x03\x04' * 1000
        self.manager.audio_buffer.extend(initial_audio)
        initial_length = len(self.manager.audio_buffer)

        # This is the audio that "arrives" during the save operation
        new_audio = b'\x05\x06\x07\x08' * 100

        # When subprocess.run (ffmpeg) is called, we'll simulate new audio arriving
        # by adding it to the buffer.
        def side_effect(*args, **kwargs):
            self.manager.audio_buffer.extend(new_audio)
            # Create a dummy MP3 file so the test can find and remove it
            filepath = f"garmin-output/garmin_recording_{mock_timestamp}.mp3"
            open(filepath, 'w').close()
        mock_subprocess_run.side_effect = side_effect

        # Call the save_recording method
        self.manager.save_recording()

        # --- Assertions ---
        # 1. The buffer should now only contain the new audio
        self.assertEqual(self.manager.audio_buffer, new_audio)
        # 2. The length of the buffer should be the length of the new audio
        self.assertEqual(len(self.manager.audio_buffer), len(new_audio))
        # 3. The initial audio should no longer be in the buffer
        self.assertNotEqual(len(self.manager.audio_buffer), initial_length)

        # Clean up the dummy file
        filepath = f"garmin-output/garmin_recording_{mock_timestamp}.mp3"
        if os.path.exists(filepath):
            os.remove(filepath)

    @patch('garmin_voice.voice_recv.VoiceRecvClient')
    async def test_join_channel(self, mock_vc):
        channel = MagicMock()
        channel.connect = AsyncMock(return_value=mock_vc)
        await self.manager.join_channel(channel)
        channel.connect.assert_called_once_with(cls=voice_recv.VoiceRecvClient)
        mock_vc.listen.assert_called_once()

    async def test_leave_channel(self):
        vc = self.manager.vc = AsyncMock()
        await self.manager.leave_channel()
        vc.disconnect.assert_called_once()
        self.assertIsNone(self.manager.vc)

if __name__ == '__main__':
    unittest.main()
