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

    @patch('time.time')
    def test_save_recording(self, mock_time):
        # Mock the timestamp
        mock_timestamp = 1234567890
        mock_time.return_value = mock_timestamp

        # Add some dummy audio data to the buffer
        self.manager.audio_buffer.extend(b'\x01\x02\x03\x04' * 1000)

        # Call the save_recording method
        self.manager.save_recording()

        # Check that the MP3 file was created
        filepath = f"garmin-output/garmin_recording_{mock_timestamp}.mp3"
        self.assertTrue(os.path.exists(filepath), f"File not found: {filepath}")

        # Check that the file is not empty
        self.assertGreater(os.path.getsize(filepath), 0)

        # Clean up the created file
        os.remove(filepath)

        # Check that the temporary WAV file was deleted
        temp_filepath = f"garmin-output/temp_full_{mock_timestamp}.wav"
        self.assertFalse(os.path.exists(temp_filepath), f"Temp file was not deleted: {temp_filepath}")

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
