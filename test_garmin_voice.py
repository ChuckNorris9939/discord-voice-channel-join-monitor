import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import time
from garmin_voice import GarminVoiceManager, BufferingSink, voice_recv

class TestGarminVoice(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.bot = MagicMock()
        self.manager = GarminVoiceManager(self.bot)

    @patch('garmin_voice.subprocess.run')
    @patch('time.time')
    def test_save_recording(self, mock_time, mock_subprocess_run):
        # Mock the timestamp
        mock_timestamp = 1234567890
        mock_time.return_value = mock_timestamp

        # Add some dummy audio data to the sink's buffer
        initial_audio = b'\x01\x02\x03\x04' * 1000
        self.manager.buffer_sink.buffer.extend(initial_audio)

        # Call the save_recording method
        self.manager.save_recording()

        # --- Assertions ---
        # 1. The buffer should now be empty after the swap
        self.assertEqual(len(self.manager.buffer_sink.buffer), 0)

        # 2. Check that ffmpeg was called
        mock_subprocess_run.assert_called_once()

        # 3. Check that the temporary WAV file was deleted
        temp_filepath = f"garmin-output/temp_full_{mock_timestamp}.wav"
        self.assertFalse(os.path.exists(temp_filepath), f"Temp file was not deleted: {temp_filepath}")

    @patch('garmin_voice.voice_recv.VoiceRecvClient')
    async def test_join_channel(self, mock_vc):
        # Mock the connect method to return our mock voice client
        channel = MagicMock()
        channel.connect = AsyncMock(return_value=mock_vc)

        # Call join_channel
        await self.manager.join_channel(channel)

        # Assert that connect was called with the right class
        channel.connect.assert_called_once_with(cls=voice_recv.VoiceRecvClient)
        # Assert that listen was called with our sink
        mock_vc.listen.assert_called_once_with(self.manager.buffer_sink)
        # Assert that the processing thread was started
        self.assertIsNotNone(self.manager._processing_thread)
        self.assertTrue(self.manager._processing_thread.is_alive())

    async def test_leave_channel(self):
        # Mock the voice client and processing thread
        mock_vc = AsyncMock()
        mock_thread = MagicMock()
        self.manager.vc = mock_vc
        self.manager._processing_thread = mock_thread

        # Call leave_channel
        await self.manager.leave_channel()

        # Assert that disconnect was called
        mock_vc.disconnect.assert_called_once()
        # Assert that the thread was joined
        mock_thread.join.assert_called_once()
        # Assert that the vc and thread are now None
        self.assertIsNone(self.manager.vc)
        self.assertIsNone(self.manager._processing_thread)

if __name__ == '__main__':
    unittest.main()
