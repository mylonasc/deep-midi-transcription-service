"""Task lifecycle tests with fake inference: no downloads or Torch required."""
import asyncio
from dataclasses import replace
import importlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mido
from fastapi.testclient import TestClient

from transc_service.config import ServerConfig
from transc_service.models import Job, JobConfig, JobStatus
from transc_service.pipeline.manager import JobManager
from transc_service.pipeline.process import compute_slot
from transc_service.stores.local import LocalObjectStore
from transc_service.stores.sqlite import SqliteMetadataStore
from transc_service.tablature.adapter import preflight, SOURCE_REVISION, MODEL_ID
from transc_service.tablature.export import export_result, measure_boundaries
from transc_service.tablature.health import model_health
from transc_service.tablature.midi import inspect_midi
from transc_service.tablature.models import TablatureOptions, TablatureResult
from transc_service.tablature.store import SqliteTablatureStore
from transc_service.tablature.tasks import TablatureManager, TabTask, TaskStatus


def source_bytes():
    midi = mido.MidiFile()
    midi.tracks.append(mido.MidiTrack([
        mido.Message("program_change", program=24), mido.Message("note_on", note=64),
        mido.Message("note_off", note=64, time=480),
    ]))
    output = BytesIO()
    midi.save(file=output)
    return output.getvalue()


def artifacts_for(data, options):
    inspected = inspect_midi(data)
    result = TablatureResult(options=options,
        provenance={"source_revision": SOURCE_REVISION, "model_id": MODEL_ID,
                    "model_revision": ServerConfig().tab_model_revision},
        source_ppq=inspected.inspection.source_ppq, tempo_map=inspected.inspection.tempo_map,
        meter_map=inspected.inspection.meter_map, source_notes=inspected.notes_by_part[options.part_id],
        assignments=[{"source_note_id": note.id, "string": 1, "fret": 0}
                     for note in inspected.notes_by_part[options.part_id]])
    output = export_result(result)
    output["report.json"] = b"{}"
    return output


class TaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = ServerConfig(data_dir=self.tmp.name)
        self.jobs = SqliteMetadataStore(self.cfg.db_path)
        self.store = SqliteTablatureStore(self.cfg.db_path)
        self.objects = LocalObjectStore(self.cfg.objects_root)
        self.compute = asyncio.Semaphore(1)
        self.manager = TablatureManager(self.store, self.jobs, self.objects, self.compute, cfg=self.cfg)
        await self.jobs.init()
        await self.store.init()
        self.data = source_bytes()
        summary = inspect_midi(self.data).inspection
        self.options = TablatureOptions(part_id=summary.parts[0].id, source_midi_sha256=summary.source_midi_sha256)
        self.job = await self.jobs.create(Job(youtube_url="https://youtu.be/x", status=JobStatus.DONE))
        self.job.midi_key = f"jobs/{self.job.id}/out.mid"
        await self.jobs.update(self.job)
        await self.objects.put_bytes(self.job.midi_key, self.data)

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self.tmp.cleanup()

    async def wait(self, task_id, predicate):
        for _ in range(300):
            task = await self.store.get(task_id)
            if predicate(task):
                return task
            await asyncio.sleep(0.01)
        self.fail("task did not reach expected state")

    async def test_success_progress_persistence_and_artifacts(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def inference(data, options, cancel, progress):
            progress("inference", 1, 2)
            entered.set()
            await release.wait()
            return artifacts_for(data, options)

        with patch("transc_service.tablature.tasks.run_tablature", inference):
            await self.manager.start()
            task = await self.manager.submit(self.job.id, self.options)
            await entered.wait()
            progressed = await self.wait(task.id, lambda task: task.completed == 1)
            self.assertEqual(progressed.phase, "inference")
            release.set()
            done = await self.wait(task.id, lambda task: task.status == TaskStatus.DONE)
        self.assertEqual(set(done.artifacts), {"json", "alphatex", "ascii", "report"})
        persisted = await SqliteTablatureStore(self.cfg.db_path).get(task.id)
        self.assertEqual(done, persisted)
        self.assertEqual((await self.jobs.get(self.job.id)).status, JobStatus.DONE)
        self.assertEqual(await self.objects.get_bytes(self.job.midi_key), self.data)
        TablatureResult.model_validate_json(await self.objects.get_bytes(done.artifacts["json"]))

    async def test_failure_then_retry_only_requeues_tablature(self):
        calls = 0

        async def inference(data, options, *args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic model failure")
            return artifacts_for(data, options)

        with patch("transc_service.tablature.tasks.run_tablature", inference):
            await self.manager.start()
            task = await self.manager.submit(self.job.id, self.options)
            failed = await self.wait(task.id, lambda task: task.status == TaskStatus.FAILED)
            self.assertIn("synthetic model failure", failed.error)
            self.assertFalse(failed.artifacts)
            results = await asyncio.gather(self.manager.retry(task.id), self.manager.retry(task.id), return_exceptions=True)
            self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
            done = await self.wait(task.id, lambda task: task.status == TaskStatus.DONE)
        self.assertEqual(done.attempt, 1)
        self.assertEqual(calls, 2)
        self.assertEqual(await self.objects.get_bytes(self.job.midi_key), self.data)

    async def test_cancel_and_delete_wait_for_producer(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def inference(data, options, cancel, progress):
            entered.set()
            await cancel.wait()
            await asyncio.sleep(0.02)
            stopped.set()
            raise asyncio.CancelledError

        with patch("transc_service.tablature.tasks.run_tablature", inference):
            await self.manager.start()
            task = await self.manager.submit(self.job.id, self.options)
            await entered.wait()
            await self.manager.delete(task.id)
            self.assertTrue(stopped.is_set())
            self.assertIsNone(await self.store.get(task.id))
            self.assertIsNone(await self.objects.get_path(f"jobs/{self.job.id}/tablatures/{task.id}"))
        self.assertEqual(self.compute._value, 1)

    async def test_cancel_waiting_for_compute_and_stale_queue(self):
        await self.compute.acquire()
        await self.manager.start()
        task = await self.manager.submit(self.job.id, self.options)
        await asyncio.sleep(0.03)
        cancelled = await asyncio.wait_for(self.manager.cancel(task.id), 1)
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertEqual(self.compute._value, 0)
        self.compute.release()
        # Cancellation before workers start leaves an old queue entry.
        await self.manager.shutdown()
        self.manager = TablatureManager(self.store, self.jobs, self.objects, self.compute, cfg=self.cfg)
        task2 = await self.manager.submit(self.job.id, self.options)
        await self.manager.cancel(task2.id)
        await self.manager.retry(task2.id)
        calls = 0

        async def inference(data, options, *args):
            nonlocal calls
            calls += 1
            return artifacts_for(data, options)

        with patch("transc_service.tablature.tasks.run_tablature", inference):
            self.manager.workers_tasks = [asyncio.create_task(self.manager._worker()) for _ in range(2)]
            await self.wait(task2.id, lambda task: task.status == TaskStatus.DONE)
            await self.manager.queue.join()
        self.assertEqual(calls, 1)

    async def test_restart_requeues_generating_task(self):
        task = TabTask(job_id=self.job.id, options=self.options, status=TaskStatus.GENERATING)
        await self.store.create(task)

        async def inference(data, options, *args):
            return artifacts_for(data, options)

        with patch("transc_service.tablature.tasks.run_tablature", inference):
            await self.manager.start()
            await self.wait(task.id, lambda task: task.status == TaskStatus.DONE)

    async def test_source_hash_change_fails_without_publication(self):
        task = await self.manager.submit(self.job.id, self.options)
        await self.objects.put_bytes(self.job.midi_key, self.data + b"changed")
        await self.manager.start()
        failed = await self.wait(task.id, lambda task: task.status == TaskStatus.FAILED)
        self.assertIn("Source MIDI changed", failed.error)
        self.assertFalse(failed.artifacts)

    async def test_partial_upload_is_not_published(self):
        original = self.objects.put_bytes
        calls = 0

        async def put(key, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic full disk")
            await original(key, data)

        async def inference(data, options, *args):
            return artifacts_for(data, options)

        with patch("transc_service.tablature.tasks.run_tablature", inference), patch.object(self.objects, "put_bytes", put):
            await self.manager.start()
            task = await self.manager.submit(self.job.id, self.options)
            failed = await self.wait(task.id, lambda task: task.status == TaskStatus.FAILED)
        self.assertFalse(failed.artifacts)
        await self.manager.delete(task.id)
        self.assertIsNone(await self.objects.get_path(f"jobs/{self.job.id}/tablatures/{task.id}"))

    async def test_changed_result_notes_are_rejected(self):
        async def inference(data, options, *args):
            output = artifacts_for(data, options)
            document = json.loads(output["tablature.json"])
            document["source_notes"][0]["velocity"] = 1
            output["tablature.json"] = json.dumps(document).encode()
            return output

        with patch("transc_service.tablature.tasks.run_tablature", inference):
            await self.manager.start()
            task = await self.manager.submit(self.job.id, self.options)
            failed = await self.wait(task.id, lambda task: task.status == TaskStatus.FAILED)
        self.assertFalse(failed.artifacts)
        self.assertIn("original MIDI", failed.error)

    async def test_parent_delete_stops_children_and_rejects_new_submissions(self):
        pipeline = JobManager(self.jobs, self.objects)
        pipeline.before_delete = self.manager.delete_parent
        first = await self.manager.submit(self.job.id, self.options)
        await pipeline.delete(self.job.id)
        self.assertIsNone(await self.store.get(first.id))
        self.assertIsNone(await self.jobs.get(self.job.id))
        self.assertIsNone(await self.objects.get_bytes(self.job.midi_key))
        with self.assertRaises(ValueError):
            await self.manager.submit(self.job.id, self.options)

    async def test_parent_deletion_waits_for_active_child(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def inference(data, options, cancel, progress):
            entered.set()
            await cancel.wait()
            await asyncio.sleep(0.02)
            stopped.set()
            raise asyncio.CancelledError

        pipeline = JobManager(self.jobs, self.objects)
        pipeline.before_delete = self.manager.delete_parent
        with patch("transc_service.tablature.tasks.run_tablature", inference):
            await self.manager.start()
            task = await self.manager.submit(self.job.id, self.options)
            await entered.wait()
            await pipeline.delete(self.job.id)
            self.assertTrue(stopped.is_set())
            self.assertIsNone(await self.store.get(task.id))
            self.assertIsNone(await self.objects.get_path(f"jobs/{self.job.id}"))

    async def test_auto_submit_is_unambiguous_and_idempotent(self):
        message = await self.manager.auto_submit(self.job)
        self.assertIn("queued", message)
        await self.manager.auto_submit(self.job)
        self.assertEqual(len(await self.store.list(self.job.id)), 1)
        midi = mido.MidiFile(file=BytesIO(self.data))
        midi.tracks.append(midi.tracks[0].copy())
        output = BytesIO()
        midi.save(file=output)
        await self.objects.put_bytes(self.job.midi_key, output.getvalue())
        message = await self.manager.auto_submit(self.job)
        self.assertIn("unambiguous", message)
        self.assertEqual(len(await self.store.list(self.job.id)), 1)

    async def test_transcription_success_survives_automatic_tab_failure(self):
        pipeline = JobManager(self.jobs, self.objects)

        async def download(job, dest, *args):
            dest = dest.with_suffix(".wav")
            dest.write_bytes(b"audio")
            return dest, {"title": "test", "duration": 1}

        async def transcribe(*args):
            return self.data, []

        async def auto(job):
            raise ValueError("model unavailable")

        pipeline.on_midi = auto
        try:
            with patch("transc_service.pipeline.manager.download_audio", download), patch(
                    "transc_service.pipeline.manager.transcribe_audio", transcribe):
                await pipeline.start()
                job = await pipeline.submit("https://youtu.be/x", JobConfig(tablature="auto"))
                for _ in range(100):
                    result = await self.jobs.get(job.id)
                    if result.auto_tablature_message:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(result.status, JobStatus.DONE)
                self.assertIn("unavailable", result.auto_tablature_message)
        finally:
            await pipeline.shutdown()

    async def test_compute_slot_cancel_releases_no_extra_permits(self):
        cancel = asyncio.Event()
        cancel.set()
        with self.assertRaises(asyncio.CancelledError):
            async with compute_slot(self.compute, cancel):
                self.fail("cancelled work ran")
        self.assertEqual(self.compute._value, 1)


class ExportTests(unittest.TestCase):
    def test_exact_export_and_unsupported_midmeasure_meter(self):
        data = source_bytes()
        summary = inspect_midi(data).inspection
        options = TablatureOptions(part_id=summary.parts[0].id, source_midi_sha256=summary.source_midi_sha256)
        document = artifacts_for(data, options)
        result = TablatureResult.model_validate_json(document["tablature.json"])
        self.assertEqual(result.notation_pages[0].end_seconds, 0.5)
        self.assertIn(b"0.1", document["score.tex"])
        result.meter_map.append(result.meter_map[0].model_copy(update={"tick": 120, "source_tick": 120, "numerator": 3}))
        with self.assertRaisesRegex(ValueError, "inside a measure"):
            measure_boundaries(result)

    def test_preflight_hash_and_note_limit(self):
        data = source_bytes()
        summary = inspect_midi(data).inspection
        options = TablatureOptions(part_id=summary.parts[0].id, source_midi_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "changed"):
            preflight(data, options, ServerConfig())
        options.source_midi_sha256 = summary.source_midi_sha256
        with self.assertRaisesRegex(ValueError, "note limit"):
            preflight(data, options, replace(ServerConfig(), tab_max_notes=0))

    def test_fractional_bpm_export_uses_positioned_bar_metadata(self):
        data = source_bytes()
        summary = inspect_midi(data).inspection
        options = TablatureOptions(part_id=summary.parts[0].id, source_midi_sha256=summary.source_midi_sha256)
        result = TablatureResult.model_validate_json(artifacts_for(data, options)["tablature.json"])
        result.tempo_map.append(result.tempo_map[0].model_copy(update={
            "tick": 120, "source_tick": 120, "time_seconds": 0.125, "microseconds_per_beat": 509740}))
        tex = export_result(result)["score.tex"].decode()
        self.assertIn(f'\\tempo ({60000000 / 509740:.15g}', tex)
        self.assertIn('"" 0.0625)', tex)
        self.assertNotIn('{tempo ', tex)  # Beat tempo effects only support integer BPM.

    def test_model_health_checks_are_offline(self):
        cfg = ServerConfig()
        with patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(model_health(cfg)["status"], "unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "__init__.py").touch()
            from transc_service.tablature.adapter import SOURCE_REVISION
            (root / "SOURCE_REVISION").write_text(SOURCE_REVISION)
            (root / "MODEL_REVISION").write_text(cfg.tab_model_revision)
            for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
                (root / name).write_text('{"model_type":"t5"}')
            (root / "model.safetensors").write_bytes(b"weights")
            spec = type("Spec", (), {"origin": str(root / "__init__.py")})()
            with patch("importlib.util.find_spec", return_value=spec), patch(
                    "transc_service.tablature.health.validator_available", return_value=True):
                self.assertEqual(model_health(replace(cfg, tab_model_dir=directory))["status"], "available")
                (root / "model.safetensors").write_bytes(b"")
                self.assertEqual(model_health(replace(cfg, tab_model_dir=directory))["status"], "unavailable")


class TaskApiTests(unittest.TestCase):
    def test_routes_retries_downloads_and_parent_deletion(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DATA_DIR": directory}):
            module = importlib.import_module("transc_service.app")
            cfg = ServerConfig(data_dir=directory)
            jobs = SqliteMetadataStore(cfg.db_path)
            objects = LocalObjectStore(cfg.objects_root)
            pipeline = JobManager(jobs, objects)

            async def start():
                await jobs.init()

            # Keep tab tasks queued so API states don't depend on model installation.
            async def tab_start(manager):
                await manager.store.init()

            with patch.object(pipeline, "start", start), patch.object(TablatureManager, "start", tab_start), patch.multiple(
                module, cfg=cfg, meta=jobs, objects=objects, manager=pipeline,
            ), TestClient(module.app) as client:
                self.assertEqual(client.get("/jobs/missing/tablatures").status_code, 404)
                job_id = client.post("/jobs", json={"youtube_url": "https://youtu.be/x"}).json()["id"]

                async def publish():
                    job = await jobs.get(job_id)
                    job.midi_key = f"jobs/{job_id}/out.mid"
                    await objects.put_bytes(job.midi_key, source_bytes())
                    await jobs.update(job)

                client.portal.call(publish)
                part = client.get(f"/jobs/{job_id}/midi-parts").json()
                body = {"part_id": part["parts"][0]["id"], "source_midi_sha256": part["source_midi_sha256"]}
                self.assertEqual(client.post(f"/jobs/{job_id}/tablatures", json={**body, "capo": 5}).status_code, 422)
                response = client.post(f"/jobs/{job_id}/tablatures", json=body)
                self.assertEqual(response.status_code, 202, response.text)
                task_id = response.json()["id"]
                self.assertEqual(len(client.get(f"/jobs/{job_id}/tablatures").json()), 1)
                self.assertEqual(client.get(f"/tablatures/{task_id}/json").status_code, 404)
                self.assertEqual(client.post(f"/tablatures/{task_id}/retry").status_code, 409)
                self.assertEqual(client.post(f"/tablatures/{task_id}/cancel").json()["status"], "cancelled")
                self.assertEqual(client.post(f"/tablatures/{task_id}/retry").json()["attempt"], 1)
                self.assertEqual(client.get("/health/tablature").status_code, 200)
                self.assertEqual(client.delete(f"/jobs/{job_id}").status_code, 204)
                self.assertEqual(client.get(f"/tablatures/{task_id}").status_code, 404)
                for route in ("retry", "cancel"):
                    self.assertEqual(client.post(f"/tablatures/missing/{route}").status_code, 404)
                self.assertEqual(client.delete("/tablatures/missing").status_code, 404)
