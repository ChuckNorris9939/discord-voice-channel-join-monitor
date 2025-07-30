import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import time
import asyncio
from garmin_voice import GarminVoiceManager, voice_recv

class TestGarminVoiceFileBased(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.bot = MagicMock()
        self.bot.loop = asyncio.get_event_loop()
        self.manager = GarminVoiceManager(self.bot)
        # Ensure the output directory exists
        os.makedirs("garmin-output", exist_ok=True)

    def tearDown(self):
        # Clean up any created files
        for f in self.manager.recording_files:
            if os.path.exists(f):
                os.remove(f)
        concat_list = "garmin-output/concat_list.txt"
        if os.path.exists(concat_list):
            os.remove(concat_list)

    @patch('garmin_voice.voice_recv.VoiceRecvClient')
    async def test_join_and_leave_channel(self, mock_vc_class):
        # Configure the mock to be an async context manager
        mock_vc_instance = AsyncMock()
        mock_vc_instance.is_connected.return_value = True

        async def connect_coro(*args, **kwargs):
            return mock_vc_instance

        channel = MagicMock()
        channel.connect = MagicMock(side_effect=connect_coro)

        # Join channel
        await self.manager.join_channel(channel)
        self.assertIsNotNone(self.manager.vc)
        self.assertIsNotNone(self.manager.recording_task)
        recording_task = self.manager.recording_task

        # Leave channel
        await self.manager.leave_channel()

        # The task is cancelled, but we need to await it to let it finish
        with self.assertRaises(asyncio.CancelledError):
            await recording_task

        self.assertIsNone(self.manager.vc)
        self.assertTrue(recording_task.cancelled())
        mock_vc_instance.disconnect.assert_awaited_once()

    @patch('garmin_voice.subprocess.run')
    def test_save_recording(self, mock_subprocess_run):
        # Create some dummy recording files
        dummy_files = ["garmin-output/chunk_1.wav", "garmin-output/chunk_2.wav"]
        for f in dummy_files:
            with open(f, 'w') as wf:
                wf.write("dummy data")

        self.manager.recording_files = dummy_files

        # Call save
        self.manager.save_recording()

        # Assert ffmpeg was called
        mock_subprocess_run.assert_called_once()
        args = mock_subprocess_run.call_args[0][0]
        self.assertIn("concat", args)
        self.assertIn("garmin-output/concat_list.txt", args)

        # Assert concat list was created and then deleted
        self.assertFalse(os.path.exists("garmin-output/concat_list.txt"))

        # Cleanup dummy files
        for f in dummy_files:
            if os.path.exists(f):
                os.remove(f)

if __name__ == '__main__':
    unittest.main()
