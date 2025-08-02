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

    def test_save_recording(self):
        # Add some dummy audio data to the buffer
        self.manager.audio_buffer.extend(b'\x01\x02\x03\x04' * 100)

        # Call the save_recording method
        self.manager.save_recording()

        # Check that the file was created
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, "garmin-output", f"garmin_recording_{int(time.time())}.wav")
        self.assertTrue(os.path.exists(filepath))

        # Check the file content
        with wave.open(filepath, 'rb') as wf:
            self.assertEqual(wf.getnchannels(), 2)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 48000)
            self.assertEqual(wf.getnframes(), 100)

        # Clean up the created file
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
