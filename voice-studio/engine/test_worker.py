"""Small CPU-free checks for the resumable engine's input/checkpoint logic."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import wave

spec = importlib.util.spec_from_file_location('voice_engine', Path(__file__).with_name('worker.py'))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_long_script_preserved_and_bounded(self):
        script = "Here's a sentence. Another, with commas; and a hyphenated-word!\n\n" * 120
        chunks = worker.split_script(script)
        self.assertGreater(len(chunks), 10)
        self.assertTrue(all(0 < len(chunk) <= 250 for chunk in chunks))
        self.assertEqual(''.join(''.join(chunks).split()), ''.join(script.split()))

    def test_oversized_word_preserved(self):
        script = 'A' * 800 + '. End.'
        chunks = worker.split_script(script)
        self.assertTrue(all(len(chunk) <= 250 for chunk in chunks))
        self.assertEqual(''.join(''.join(chunks).split()), ''.join(script.split()))

    def test_job_id_rejects_path(self):
        with self.assertRaises(worker.JobError):
            worker.canonical_job_id('../../other')

    def test_real_server_job_id(self):
        value = '1794400000000-3f147092-5148-4f6a-8a35-15dbd14daebd'
        self.assertEqual(value, worker.canonical_job_id(value))
        with self.assertRaises(worker.JobError):
            worker.canonical_job_id(value + '/other')

    def test_late_heartbeat_does_not_cancel_new_job(self):
        class LateAPI:
            def request(self, *args, **kwargs):
                engine.active_job_id = 'new-job'
                worker.STOP.set()
                return {'cancelRequested': True}
        engine = worker.Engine(LateAPI())
        engine.active_job_id = 'old-job'
        try:
            engine.heartbeat()
            self.assertFalse(engine.cancel_requested.is_set())
        finally:
            worker.STOP.clear()

    def test_script_limits(self):
        with self.assertRaises(worker.JobError):
            worker.validate_script('word ' * 6001)
        with self.assertRaises(worker.JobError):
            worker.validate_script(' ')

    def test_checkpoint_is_verified_by_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chunk.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(24000)
                stream.writeframes(b'\x01\x00' * 2400)
            info = worker.wav_info(path)
            self.assertTrue(worker.valid_checkpoint(path, info))
            with path.open('ab') as stream:
                stream.write(b'changed')
            self.assertFalse(worker.valid_checkpoint(path, info))


if __name__ == '__main__':
    unittest.main()
