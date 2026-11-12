"""Opt-in real model + API/store/subprocess/publication/deletion smoke test.

Run with the same model/cache settings as smoke_tablature.py. Uses temporary
metadata/artifacts and synthetic MIDI; never submits a YouTube download.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from tools.smoke_tablature import fixture
from transc_service.models import Job, JobStatus


with tempfile.TemporaryDirectory() as directory:
    os.environ["DATA_DIR"] = directory
    from transc_service import app as service

    with TestClient(service.app) as client:
        async def publish():
            job = await service.meta.create(Job(youtube_url="https://youtu.be/synthetic",
                                               status=JobStatus.DONE, midi_key="jobs/synthetic/out.mid"))
            await service.objects.put_bytes(job.midi_key, fixture())
            return job

        job = client.portal.call(publish)
        inspection = client.get(f"/jobs/{job.id}/midi-parts").json()
        response = client.post(f"/jobs/{job.id}/tablatures", json={"part_id": inspection["parts"][0]["id"],
            "source_midi_sha256": inspection["source_midi_sha256"]})
        assert response.status_code == 202, response.text
        task_id = response.json()["id"]

        async def wait():
            async with asyncio.timeout(90):
                while True:
                    task = await service.tabs.store.get(task_id)
                    if task.status in {"done", "failed", "cancelled"}:
                        return task
                    await asyncio.sleep(0.05)

        task = client.portal.call(wait)
        assert task.status == "done", task.error
        for kind in ("json", "alphatex", "ascii", "report"):
            download = client.get(f"/tablatures/{task_id}/{kind}")
            assert download.status_code == 200 and download.content
        assert client.get(f"/jobs/{job.id}/midi").content == fixture()
        assert client.post(f"/tablatures/{task_id}/retry").status_code == 409
        assert client.delete(f"/jobs/{job.id}").status_code == 204
        assert client.get(f"/tablatures/{task_id}").status_code == 404
        print("Real API smoke passed: model → independent notation validation → persisted artifacts → downloads → parent deletion")
