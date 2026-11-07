"""Queue orchestration. Stage work is isolated in killable subprocesses."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from ..models import TERMINAL, Job, JobConfig, JobStatus
from ..stores import MetadataStore, ObjectStore
from .downloader import download_audio
from .transcriber import transcribe_audio

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self, meta: MetadataStore, objects: ObjectStore, workers: int = 1):
        if workers < 1:
            raise ValueError("workers must be positive")
        self._meta, self._objects, self._workers = meta, objects, workers
        self._queue = asyncio.Queue()
        self._cancel = {}
        self._active = {}
        self._tasks = []
        self._stopping = False

    async def start(self):
        self._stopping = False
        await self._meta.init()
        offset = 0
        while True:
            jobs = await self._meta.list(limit=500, offset=offset)
            for job in jobs:
                if job.status not in TERMINAL:
                    job.status = JobStatus.QUEUED
                    await self._meta.update(job)
                    self._cancel[job.id] = asyncio.Event()
                    self._queue.put_nowait(job.id)
            if len(jobs) < 500:
                break
            offset += len(jobs)
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(self._workers)]

    async def shutdown(self):
        # Stop accepting work first (ASGI lifespan), then stop children before exiting.
        self._stopping = True
        for event in self._cancel.values():
            event.set()
        await asyncio.gather(*list(self._active.values()), return_exceptions=True)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def submit(self, youtube_url: str, config: JobConfig | None = None):
        job = await self._meta.create(Job(youtube_url=youtube_url, config=config or JobConfig()))
        self._cancel[job.id] = asyncio.Event()
        self._queue.put_nowait(job.id)
        return job

    async def cancel(self, job_id):
        job = await self._meta.get(job_id)
        if job is None or job.status in TERMINAL:
            return job
        self._cancel.setdefault(job_id, asyncio.Event()).set()
        active = self._active.get(job_id)
        if active:
            await asyncio.shield(active)
        job = await self._meta.get(job_id)
        if job and job.status not in TERMINAL:
            job.status = JobStatus.CANCELLED
            await self._meta.update(job)
        return job

    async def delete(self, job_id):
        if await self.cancel(job_id) is None:
            return False
        # The producer is now stopped, so it cannot resurrect artifacts after deletion.
        await self._objects.delete_prefix(f"jobs/{job_id}")
        await self._meta.delete(job_id)
        self._cancel.pop(job_id, None)
        return True

    async def _worker(self):
        while not self._stopping:
            job_id = await self._queue.get()
            task = asyncio.create_task(self._run(job_id))
            self._active[job_id] = task
            try:
                await task
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Worker failed for job %s", job_id)
            finally:
                self._active.pop(job_id, None)
                self._queue.task_done()

    async def _run(self, job_id):
        job = await self._meta.get(job_id)
        if job is None or job.status in TERMINAL:
            return
        cancel = self._cancel.setdefault(job_id, asyncio.Event())

        async def persist_progress():
            while True:
                await asyncio.sleep(0.5)
                await self._meta.update(job)

        progress = asyncio.create_task(persist_progress())
        try:
            if cancel.is_set():
                raise asyncio.CancelledError
            with tempfile.TemporaryDirectory() as directory:
                tmp = Path(directory)
                # Reuse completed downloads after a restart; ObjectStore needn't expose local paths.
                audio = await self._objects.get_bytes(job.audio_key) if job.audio_key else None
                if audio is None:
                    job.status = JobStatus.DOWNLOADING
                    await self._meta.update(job)

                    def dl_progress(fraction):
                        job.progress.download_progress = fraction

                    source, info = await download_audio(job, tmp / "source", cancel, dl_progress)
                    job.title, job.duration_s = info.get("title"), info.get("duration")
                    job.audio_key = f"jobs/{job.id}/source{source.suffix}"
                    await self._objects.put_path(job.audio_key, source)
                    job.progress.download_progress = 1.0
                    job.status = JobStatus.DOWNLOADED
                    await self._meta.update(job)
                else:
                    source = tmp / Path(job.audio_key).name
                    source.write_bytes(audio)
                if cancel.is_set():
                    raise asyncio.CancelledError
                job.status = JobStatus.TRANSCRIBING
                await self._meta.update(job)

                def chunk_progress(done, total):
                    job.progress.chunks_completed, job.progress.chunks_total = done, total

                midi, events = await transcribe_audio(source, job.config.transcription, cancel, chunk_progress)
                if cancel.is_set():
                    raise asyncio.CancelledError
                job.midi_key = f"jobs/{job.id}/out.mid"
                job.events_key = f"jobs/{job.id}/events.json"
                await self._objects.put_bytes(job.midi_key, midi)
                await self._objects.put_bytes(job.events_key, json.dumps(events).encode())
                job.status, job.error = JobStatus.DONE, None
        except asyncio.CancelledError:
            job.status, job.error = JobStatus.CANCELLED, "cancelled"
        except Exception as exc:
            job.status = JobStatus.CANCELLED if cancel.is_set() else JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            progress.cancel()
            await asyncio.gather(progress, return_exceptions=True)
            await self._meta.update(job)
            self._cancel.pop(job_id, None)
