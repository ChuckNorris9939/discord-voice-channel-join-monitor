import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import time
import asyncio
from garmin_voice import GarminVoiceManager, NativeVoiceClient

class TestGarminVoiceAudioRec(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.bot = MagicMock()
        self.manager = GarminVoiceManager(self.bot)
        os.makedirs("garmin-output", exist_ok=True)

    def tearDown(self):
        # Clean up any created files
        timestamp = int(time.time()) # This is a bit of a hack to guess the filename
        filename_wav = f"garmin-output/garmin_recording_{timestamp}.wav"
        filename_mp3 = f"garmin-output/garmin_recording_{timestamp}.mp3"
        if os.path.exists(filename_wav):
            os.remove(filename_wav)
        if os.path.exists(filename_mp3):
            os.remove(filename_mp3)

    async def test_join_and_leave_channel(self):
        # Mock the connect method to return a mock NativeVoiceClient
        mock_vc_instance = AsyncMock(spec=NativeVoiceClient)
        channel = MagicMock()
        channel.connect = AsyncMock(return_value=mock_vc_instance)

        # Join
        await self.manager.join_channel(channel)
        channel.connect.assert_awaited_once_with(cls=NativeVoiceClient)
        self.assertIs(self.manager.vc, mock_vc_instance)
        self.manager.vc.record.assert_called_once()

        # Leave
        mock_vc = self.manager.vc
        await self.manager.leave_channel()
        mock_vc.stop_record.assert_awaited_once()
        mock_vc.disconnect.assert_awaited_once()
        self.assertIsNone(self.manager.vc)

    @patch('garmin_voice.subprocess.run')
    @patch('time.time')
    async def test_save_recording(self, mock_time, mock_subprocess_run):
        mock_timestamp = 1234567890
        mock_time.return_value = mock_timestamp

        self.manager.vc = AsyncMock(spec=NativeVoiceClient)
        self.manager.vc.is_recording.return_value = True
        self.manager.vc.stop_record.return_value = b"dummy_wav_bytes"

        await self.manager.save_recording()

        # Check that recording was stopped and restarted
        self.manager.vc.stop_record.assert_awaited_once()
        self.manager.vc.record.assert_called_once()

        # Check that the WAV file was created and then deleted
        filename_wav = f"garmin-output/garmin_recording_{mock_timestamp}.wav"
        self.assertFalse(os.path.exists(filename_wav))

        # Check that ffmpeg was called correctly
        mock_subprocess_run.assert_called_once()
        args = mock_subprocess_run.call_args[0][0]
        self.assertIn(filename_wav, args)
        self.assertIn(os.path.join("garmin-output", f"garmin_recording_{mock_timestamp}.mp3"), args)

if __name__ == '__main__':
    unittest.main()
