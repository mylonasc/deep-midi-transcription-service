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


class ConfigurationTests(unittest.TestCase):
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
