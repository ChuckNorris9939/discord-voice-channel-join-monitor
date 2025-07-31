import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import time
import asyncio
from garmin_voice import GarminVoiceManager, NativeVoiceClient

class TestGarminVoiceFinal(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.bot = MagicMock()
        self.bot.loop = asyncio.get_event_loop()
        self.manager = GarminVoiceManager(self.bot)
        os.makedirs("garmin-output", exist_ok=True)

    def tearDown(self):
        # Clean up any created files
        timestamp = int(time.time())
        filename_wav = f"garmin-output/garmin_recording_{timestamp}.wav"
        filename_mp3 = f"garmin-output/garmin_recording_{timestamp}.mp3"
        if os.path.exists(filename_wav):
            os.remove(filename_wav)
        if os.path.exists(filename_mp3):
            os.remove(filename_mp3)

    async def test_join_and_leave_channel(self):
        channel = MagicMock()
        mock_vc = AsyncMock(spec=NativeVoiceClient)
        channel.connect = AsyncMock(return_value=mock_vc)

        # Join
        await self.manager.join_channel(channel)
        channel.connect.assert_awaited_once_with(cls=NativeVoiceClient)
        self.assertIsNotNone(self.manager.stt_task)
        self.assertIsNotNone(self.manager.trim_task)

        # Leave
        stt_task = self.manager.stt_task
        trim_task = self.manager.trim_task
        await self.manager.leave_channel()
        await asyncio.sleep(0)
        self.assertTrue(stt_task.cancelled())
        self.assertTrue(trim_task.cancelled())
        mock_vc.disconnect.assert_awaited_once()

    @patch('garmin_voice.subprocess.run')
    @patch('time.time')
    def test_save_recording(self, mock_time, mock_subprocess_run):
        mock_timestamp = 1234567890
        mock_time.return_value = mock_timestamp

        self.manager.audio_buffer.extend(b"dummy_wav_bytes")
        self.manager.save_recording()

        # Check that ffmpeg was called
        mock_subprocess_run.assert_called_once()
        args = mock_subprocess_run.call_args[0][0]
        filename_wav = f"garmin-output/garmin_recording_{mock_timestamp}.wav"
        self.assertIn(filename_wav, args)

        # Check that the WAV file was created and then deleted
        self.assertFalse(os.path.exists(filename_wav))

if __name__ == '__main__':
    unittest.main()
