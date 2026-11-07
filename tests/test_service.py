"""Regression tests run without YouTube access or model downloads."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from transc_service.models import JobConfig, JobStatus, TranscriptionOptions
from transc_service.pipeline.downloader import _is_mix
from transc_service.pipeline.manager import JobManager
from transc_service.pipeline.process import run_stage
from transc_service.stores.local import LocalObjectStore
from transc_service.stores.sqlite import SqliteMetadataStore


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = SqliteMetadataStore(str(self.root / "meta.db"))
        self.objects = LocalObjectStore(self.root / "objects")
        self.manager = JobManager(self.meta, self.objects)

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.tmp.cleanup()

    async def wait_for(self, job_id, predicate):
        for _ in range(200):
            job = await self.meta.get(job_id)
            if predicate(job):
                return job
            await asyncio.sleep(0.02)
        self.fail("job did not reach expected state")

    async def test_progress_artifacts_and_restart(self):
        release = asyncio.Event()

        async def download(job, dest, cancel, progress):
            progress(0.5)
            await release.wait()
            dest = dest.with_suffix(".m4a")
            dest.write_bytes(b"audio")
            return dest, {"title": "song", "duration": 2}

        async def transcribe(*args):
            args[-1](1, 1)
            return b"midi", [{"type": "note_start", "index": 1}]

        with patch("transc_service.pipeline.manager.download_audio", download), patch(
            "transc_service.pipeline.manager.transcribe_audio", transcribe
        ):
            await self.manager.start()
            job = await self.manager.submit("https://youtu.be/example")
            await self.wait_for(job.id, lambda j: j.progress.download_progress == 0.5)
            release.set()
            done = await self.wait_for(job.id, lambda j: j.status == JobStatus.DONE)
            self.assertEqual(await self.objects.get_bytes(done.audio_key), b"audio")
            await self.manager.shutdown()
            reopened = SqliteMetadataStore(str(self.root / "meta.db"))
            await reopened.init()
            persisted = await reopened.get(job.id)
            self.assertEqual(json.loads(await self.objects.get_bytes(persisted.events_key))[0]["index"], 1)

    async def test_cancel_and_delete_wait_for_producer(self):
        stopped = asyncio.Event()

        async def download(job, dest, cancel, progress):
            await cancel.wait()
            await asyncio.sleep(0.05)
            stopped.set()
            raise asyncio.CancelledError

        with patch("transc_service.pipeline.manager.download_audio", download):
            await self.manager.start()
            job = await self.manager.submit("https://youtu.be/example")
            await self.wait_for(job.id, lambda j: j.status == JobStatus.DOWNLOADING)
            self.assertTrue(await self.manager.delete(job.id))
            self.assertTrue(stopped.is_set())
            self.assertIsNone(await self.meta.get(job.id))
            self.assertIsNone(await self.objects.get_path(f"jobs/{job.id}"))
            self.assertTrue(all(not t.done() for t in self.manager._tasks))

    async def test_queued_cancel(self):
        await self.meta.init()
        job = await self.manager.submit("https://youtu.be/example")
        result = await self.manager.cancel(job.id)
        self.assertEqual(result.status, JobStatus.CANCELLED)

    async def test_retry_reuses_download_and_persists_attempt(self):
        calls = 0
        async def download(job, dest, cancel, progress):
            dest = dest.with_suffix('.m4a')
            dest.write_bytes(b'audio')
            return dest, {'title': 'Song', 'duration': 2}
        async def transcribe(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError('temporary inference error')
            return b'midi', []
        with patch('transc_service.pipeline.manager.download_audio', download) as downloader, patch(
            'transc_service.pipeline.manager.transcribe_audio', transcribe
        ):
            await self.manager.start()
            job = await self.manager.submit('https://youtu.be/example')
            failed = await self.wait_for(job.id, lambda j: j.status == JobStatus.FAILED)
            self.assertIsNotNone(failed.audio_key)
            # A second download must not happen.
            async def unexpected_download(*args):
                self.fail('retry downloaded audio again')
            with patch('transc_service.pipeline.manager.download_audio', unexpected_download):
                retried = await self.manager.retry(job.id)
                self.assertEqual(retried.id, job.id)
                self.assertEqual(retried.retry_count, 1)
                self.assertIsNone(retried.error)
                done = await self.wait_for(job.id, lambda j: j.status == JobStatus.DONE)
                self.assertEqual(done.audio_key, failed.audio_key)
                self.assertEqual((await self.meta.get(job.id)).retry_count, 1)
                with self.assertRaises(ValueError):
                    await self.manager.retry(job.id)

    async def test_retry_rejects_duplicate_and_skips_stale_queue_entries(self):
        await self.meta.init()
        job = await self.manager.submit('https://youtu.be/example')
        await self.manager.cancel(job.id)
        results = await asyncio.gather(self.manager.retry(job.id), self.manager.retry(job.id), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        # Start workers without requeueing from DB, retaining the stale entry.
        calls = 0
        async def download(job, dest, cancel, progress):
            nonlocal calls
            calls += 1
            dest = dest.with_suffix('.m4a')
            dest.write_bytes(b'audio')
            return dest, {}
        async def transcribe(*args):
            return b'midi', []
        with patch('transc_service.pipeline.manager.download_audio', download), patch(
            'transc_service.pipeline.manager.transcribe_audio', transcribe
        ):
            self.manager._tasks = [asyncio.create_task(self.manager._worker()) for _ in range(2)]
            await self.wait_for(job.id, lambda j: j.status == JobStatus.DONE)
            await self.manager._queue.join()
        self.assertEqual(calls, 1)

    async def test_subprocess_cancel_interrupts_blocked_stage(self):
        original = asyncio.create_subprocess_exec
        children = []

        async def spawn(*args, **kwargs):
            child = await original(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
            children.append(child)
            return child

        cancel = asyncio.Event()
        with patch("asyncio.create_subprocess_exec", spawn):
            task = asyncio.create_task(run_stage("download", {}, self.root, cancel))
            await asyncio.sleep(0.1)
            cancel.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        self.assertIsNotNone(children[0].returncode)

    async def test_unsafe_object_key(self):
        with self.assertRaises(ValueError):
            await self.objects.put_bytes("../outside", b"bad")

    async def test_progress_output_cannot_hide_stage_result(self):
        original = asyncio.create_subprocess_exec
        script = """
import sys
sys.stderr.write('[download] 100%' + '\\r' + 'x' * 100000)
sys.stderr.flush()
print('SERVICE_EVENT:{"type":"progress","value":[1.0]}', flush=True)
sys.stderr.write('download complete\\r')
sys.stderr.flush()
print('SERVICE_EVENT:{"type":"result","value":{"path":"source.m4a"}}', flush=True)
"""
        async def spawn(*args, **kwargs):
            return await original(sys.executable, "-c", script, **kwargs)

        progress = []
        with patch("asyncio.create_subprocess_exec", spawn):
            result = await asyncio.wait_for(run_stage("download", {}, self.root,
                asyncio.Event(), progress.append), timeout=5)
        self.assertEqual(result, {"path": "source.m4a"})
        self.assertEqual(progress, [1.0])

    async def test_failed_stage_preserves_stderr_diagnostics(self):
        original = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            return await original(sys.executable, "-c",
                "import sys; print('decoder failed', file=sys.stderr); sys.exit(2)", **kwargs)
        with patch("asyncio.create_subprocess_exec", spawn):
            with self.assertRaisesRegex(RuntimeError, r"exit 2.*decoder failed"):
                await run_stage("download", {}, self.root, asyncio.Event())


class ConfigurationTests(unittest.TestCase):
    def test_local_weights_configuration(self):
        from transc_service.config import ServerConfig
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / 'model.safetensors'
            config = ServerConfig(weights_medium=str(weights))
            with self.assertRaisesRegex(ValueError, 'weights not found'):
                config.weights_for('medium')
            weights.write_bytes(b'weights')
            with self.assertRaisesRegex(ValueError, 'config.json'):
                config.weights_for('medium')
            weights.with_name('config.json').write_text('{}')
            self.assertEqual(config.weights_for('medium'), str(weights))
            self.assertEqual(config.weights_for('small'), 'small')
            with patch.dict('os.environ', {'MUSCRIPTOR_WEIGHTS_MEDIUM': str(weights)}):
                self.assertEqual(ServerConfig().weights_for('medium'), str(weights))

    def test_configuration_validation(self):
        with self.assertRaises(ValueError):
            JobConfig(yt_dlp={"max_duration_s": -1})
        with self.assertRaises(ValueError):
            TranscriptionOptions(batch_size=4)
        self.assertEqual(TranscriptionOptions(batch_size=4, prelude_forcing=False).batch_size, 4)
        with self.assertRaises(ValueError):
            JobConfig(version=99)
        with self.assertRaises(ValueError):
            JobConfig(yt_dlp={"allow_playlist": True})

    def test_mix_detection_handles_null_metadata_and_remixes(self):
        self.assertTrue(_is_mix({"title": "Summer Mix", "playlist_title": None}))
        self.assertFalse(_is_mix({"title": "Song (Remix)", "playlist_title": None}))


if __name__ == "__main__":
    unittest.main()
