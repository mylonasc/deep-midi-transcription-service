"""Independent durable tablature tasks; same compute budget as transcription."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Protocol
import uuid

from pydantic import Field

from ..config import ServerConfig
from ..stores import MetadataStore, ObjectStore
from .adapter import preflight, run_tablature, SOURCE_REVISION, MODEL_ID
from .models import StrictModel, TablatureOptions, TablatureResult

log = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
ARTIFACTS = {"json": "tablature.json", "alphatex": "score.tex", "ascii": "preview.txt", "report": "report.json"}


class TabTask(StrictModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    job_id: str
    options: TablatureOptions
    model_revision: str = "b8edf84f4b76a8bc03bc1d78255c77364fba72eb"
    source_revision: str = SOURCE_REVISION
    status: TaskStatus = TaskStatus.QUEUED
    attempt: int = 0
    phase: str = "queued"
    completed: int = 0
    total: int = 0
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TablatureStore(Protocol):
    async def init(self) -> None: ...
    async def create(self, task: TabTask) -> None: ...
    async def get(self, task_id: str) -> TabTask | None: ...
    async def list(self, job_id: str | None = None) -> list[TabTask]: ...
    async def update(self, task: TabTask) -> None: ...
    async def delete(self, task_id: str) -> None: ...


class TablatureManager:
    def __init__(self, store: TablatureStore, jobs: MetadataStore, objects: ObjectStore,
                 compute: asyncio.Semaphore, workers=1, cfg: ServerConfig | None = None):
        self.store, self.jobs, self.objects, self.compute = store, jobs, objects, compute
        self.cfg = cfg or ServerConfig()
        self.workers = workers
        self.queue = asyncio.Queue()
        self.cancel_events = {}
        self.active = {}
        self.workers_tasks = []
        self.lock = asyncio.Lock()
        self.auto_lock = asyncio.Lock()
        self.deleting_parents = set()
        self.stopping = False

    async def start(self):
        self.stopping = False
        await self.store.init()
        for task in await self.store.list():
            if task.status not in TERMINAL:
                task.status, task.phase = TaskStatus.QUEUED, "queued"
                await self.store.update(task)
                self.cancel_events[task.id] = asyncio.Event()
                self.queue.put_nowait((task.id, task.attempt))
        self.workers_tasks = [asyncio.create_task(self._worker()) for _ in range(self.workers)]

    async def shutdown(self):
        self.stopping = True
        for event in self.cancel_events.values():
            event.set()
        await asyncio.gather(*list(self.active.values()), return_exceptions=True)
        for worker in self.workers_tasks:
            worker.cancel()
        await asyncio.gather(*self.workers_tasks, return_exceptions=True)

    async def submit(self, job_id, options):
        async with self.lock:
            if self.stopping or job_id in self.deleting_parents:
                raise ValueError("Job is being deleted or service is stopping")
            job = await self.jobs.get(job_id)
            if job is None:
                raise LookupError("job not found")
            data = await self.objects.get_bytes(job.midi_key) if job.midi_key else None
            if data is None:
                raise ValueError("MIDI is not available")
            await asyncio.to_thread(preflight, data, options, self.cfg)
            task = TabTask(job_id=job_id, options=options, model_revision=self.cfg.tab_model_revision)
            await self.store.create(task)
            self.cancel_events[task.id] = asyncio.Event()
            self.queue.put_nowait((task.id, task.attempt))
            return task

    async def retry(self, task_id):
        async with self.lock:
            task = await self.store.get(task_id)
            if task is None:
                raise LookupError("tablature task not found")
            if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                raise ValueError("Only failed or cancelled tablature tasks can be retried")
            if task.job_id in self.deleting_parents or self.stopping:
                raise ValueError("Job is being deleted or service is stopping")
            if task_id in self.active:
                await asyncio.shield(self.active[task_id])
            task.status, task.phase, task.error = TaskStatus.QUEUED, "queued", None
            task.completed, task.total, task.artifacts = 0, 0, {}
            task.attempt += 1
            await self.store.update(task)
            self.cancel_events[task.id] = asyncio.Event()
            self.queue.put_nowait((task.id, task.attempt))
            return task

    async def _cancel(self, task_id):
        task = await self.store.get(task_id)
        if task is None:
            raise LookupError("tablature task not found")
        if task.status not in TERMINAL:
            self.cancel_events.setdefault(task_id, asyncio.Event()).set()
            if task_id in self.active:
                await asyncio.shield(self.active[task_id])
            task = await self.store.get(task_id)
            if task.status not in TERMINAL:
                task.status, task.phase, task.error = TaskStatus.CANCELLED, "cancelled", "cancelled"
                await self.store.update(task)
        return task

    async def cancel(self, task_id):
        async with self.lock:
            return await self._cancel(task_id)

    async def delete(self, task_id):
        async with self.lock:
            task = await self._cancel(task_id)
            await self.objects.delete_prefix(f"jobs/{task.job_id}/tablatures/{task.id}")
            await self.store.delete(task.id)
            self.cancel_events.pop(task.id, None)

    async def delete_parent(self, job_id):
        async with self.lock:
            self.deleting_parents.add(job_id)
            for task in await self.store.list(job_id):
                await self._cancel(task.id)
                await self.objects.delete_prefix(f"jobs/{job_id}/tablatures/{task.id}")
                await self.store.delete(task.id)
                self.cancel_events.pop(task.id, None)

    async def auto_submit(self, job):
        async with self.auto_lock:
            return await self._auto_submit(job)

    async def _auto_submit(self, job):
        from .midi import inspect_midi
        data = await self.objects.get_bytes(job.midi_key)
        if data is None:
            return "Automatic tablature: MIDI not available."
        inspection = (await asyncio.to_thread(inspect_midi, data)).inspection
        eligible = [part for part in inspection.parts if part.guitar_program
                    and not any(item.blocks_generation for item in part.diagnostics + inspection.diagnostics)]
        if len(eligible) != 1:
            return "Automatic tablature needs one unambiguous eligible guitar part; select a part in Visualization."
        # Recovery may invoke this hook again: don't create a duplicate automatic task.
        existing = await self.store.list(job.id)
        if not any(task.options.source_midi_sha256 == inspection.source_midi_sha256 for task in existing):
            await self.submit(job.id, TablatureOptions(part_id=eligible[0].id,
                                                     source_midi_sha256=inspection.source_midi_sha256))
        return "Automatic tablature queued; see independent task status in Visualization."

    async def _worker(self):
        while not self.stopping:
            task_id, attempt = await self.queue.get()
            try:
                task = await self.store.get(task_id)
                if task is None or task.attempt != attempt or task.status != TaskStatus.QUEUED or task_id in self.active:
                    continue
                work = asyncio.create_task(self._run(task_id, attempt))
                self.active[task_id] = work
                try:
                    await work
                finally:
                    if self.active.get(task_id) is work:
                        self.active.pop(task_id, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Tablature worker failed")
            finally:
                self.queue.task_done()

    async def _run(self, task_id, attempt):
        task = await self.store.get(task_id)
        if task is None or task.status != TaskStatus.QUEUED or task.attempt != attempt:
            return
        cancel = self.cancel_events.setdefault(task_id, asyncio.Event())

        async def heartbeat():
            while True:
                await asyncio.sleep(0.5)
                await self.store.update(task)

        progress_task = asyncio.create_task(heartbeat())
        try:
            # Waiting for compute must also be cancellable (including shutdown).
            acquired = asyncio.create_task(self.compute.acquire())
            cancelled = asyncio.create_task(cancel.wait())
            try:
                await asyncio.wait({acquired, cancelled}, return_when=asyncio.FIRST_COMPLETED)
                if cancel.is_set():
                    raise asyncio.CancelledError
                await acquired
                task.status, task.phase = TaskStatus.GENERATING, "preflight"
                await self.store.update(task)
                if task.model_revision != self.cfg.tab_model_revision or task.source_revision != SOURCE_REVISION:
                    raise ValueError("Pinned inference revision changed; create a new tablature task")
                job = await self.jobs.get(task.job_id)
                data = await self.objects.get_bytes(job.midi_key) if job and job.midi_key else None
                if data is None:
                    raise ValueError("Source MIDI no longer available")
                inspected = await asyncio.to_thread(preflight, data, task.options, self.cfg)

                def progress(phase, done, total):
                    task.phase, task.completed, task.total = phase, done, total

                artifacts = await run_tablature(data, task.options, cancel, progress)
                if set(artifacts) != set(ARTIFACTS.values()):
                    raise ValueError("Incomplete tablature artifacts")
                canonical = TablatureResult.model_validate_json(artifacts["tablature.json"])
                if canonical.options != task.options:
                    raise ValueError("Tablature output configuration differs from request")
                if (canonical.provenance.model_revision != task.model_revision
                        or canonical.provenance.source_revision != task.source_revision
                        or canonical.provenance.model_id != MODEL_ID):
                    raise ValueError("Tablature model provenance differs from pinned task")
                if (canonical.source_notes != inspected.notes_by_part[task.options.part_id]
                        or canonical.tempo_map != inspected.inspection.tempo_map
                        or canonical.meter_map != inspected.inspection.meter_map
                        or canonical.source_ppq != inspected.inspection.source_ppq):
                    raise ValueError("Tablature output differs from original MIDI notes/timing")
                keys = {}
                for kind, filename in ARTIFACTS.items():
                    if cancel.is_set():
                        raise asyncio.CancelledError
                    key = f"jobs/{task.job_id}/tablatures/{task.id}/attempt-{attempt}/{filename}"
                    await self.objects.put_bytes(key, artifacts[filename])
                    keys[kind] = key
                if cancel.is_set():
                    raise asyncio.CancelledError
                task.artifacts, task.status, task.phase, task.error = keys, TaskStatus.DONE, "complete", None
            finally:
                cancelled.cancel()
                if not acquired.done():
                    acquired.cancel()
                await asyncio.gather(acquired, cancelled, return_exceptions=True)
                if not acquired.cancelled() and acquired.exception() is None:
                    self.compute.release()
        except asyncio.CancelledError:
            task.status, task.phase, task.error = TaskStatus.CANCELLED, "cancelled", "cancelled"
        except Exception as exc:
            task.status = TaskStatus.CANCELLED if cancel.is_set() else TaskStatus.FAILED
            task.error = f"{type(exc).__name__}: {exc}"[-6000:]
        finally:
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)
            await self.store.update(task)
