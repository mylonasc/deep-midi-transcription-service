"""FastAPI bootstrap service. Run: uvicorn transc_service.app:app"""
from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlparse
import mimetypes
import asyncio
from hashlib import sha256

from fastapi import FastAPI, HTTPException, Response, Query
from pydantic import BaseModel

from .config import ServerConfig
from .models import JobConfig, JobStatus
from .model_health import model_availability
from .pipeline.manager import JobManager
from .stores.local import LocalObjectStore
from .stores.sqlite import SqliteMetadataStore
from .tablature.midi import MidiInspectionError, inspect_midi
from .tablature.models import MidiInspection
from .tablature.models import TablatureOptions
from .tablature.store import SqliteTablatureStore
from .tablature.tasks import TablatureManager, TabTask, ARTIFACTS

cfg = ServerConfig()
meta = SqliteMetadataStore(cfg.db_path)
objects = LocalObjectStore(cfg.objects_root)
manager = JobManager(meta, objects, workers=cfg.workers)
tabs: TablatureManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tabs
    tabs = TablatureManager(SqliteTablatureStore(cfg.db_path), meta, objects, manager.compute,
                            workers=cfg.workers, cfg=cfg)
    manager.on_midi = tabs.auto_submit
    manager.before_delete = tabs.delete_parent
    await meta.init()
    await tabs.start()
    await manager.start()
    # Recover the small crash window between MIDI completion and automatic submit.
    offset = 0
    while True:
        jobs = await meta.list(limit=500, offset=offset)
        for job in jobs:
            if job.status == JobStatus.DONE and job.midi_key and job.config.tablature == "auto" and not job.auto_tablature_message:
                try:
                    job.auto_tablature_message = await tabs.auto_submit(job)
                except Exception as exc:
                    job.auto_tablature_message = f"Automatic tablature unavailable: {type(exc).__name__}"
                await meta.update(job)
        if len(jobs) < 500:
            break
        offset += len(jobs)
    try:
        yield
    finally:
        await tabs.shutdown()
        await manager.shutdown()


app = FastAPI(title="transcription-service (bootstrap)", lifespan=lifespan)


class CreateJobBody(BaseModel):
    youtube_url: str
    config: JobConfig | None = None


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/health/models")
async def model_health():
    return await asyncio.to_thread(model_availability, cfg)


@app.get("/health/tablature")
async def tablature_health():
    from .tablature.health import model_health
    return await asyncio.to_thread(model_health, cfg)


@app.post("/jobs", status_code=202)
async def create_job(body: CreateJobBody):
    parsed = urlparse(body.youtube_url)
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(400, "invalid URL port")
    if parsed.scheme != "https" or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
    } or parsed.username or parsed.password or port not in (None, 443):
        raise HTTPException(400, "expected an HTTPS YouTube URL")
    defaults = JobConfig.model_validate({"transcription": {"model": cfg.default_model},
        "yt_dlp": {"max_duration_s": cfg.default_max_duration_s}}).model_dump()
    if body.config:
        for key, value in body.config.model_dump(exclude_unset=True).items():
            if isinstance(value, dict) and isinstance(defaults.get(key), dict):
                defaults[key].update(value)
            else:
                defaults[key] = value
    job = await manager.submit(body.youtube_url, JobConfig.model_validate(defaults))
    return job


@app.get("/jobs")
async def list_jobs(status: JobStatus | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return await meta.list(status=status, limit=min(limit, 500), offset=offset)


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await meta.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = await manager.cancel(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str):
    ok = await manager.delete(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return Response(status_code=204)


@app.post("/jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: str):
    try:
        job = await manager.retry(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if job is None:
        raise HTTPException(404, "job not found")
    return job


async def _artifact_bytes(key: str | None, not_ready_msg: str) -> bytes:
    if not key:
        raise HTTPException(404, not_ready_msg)
    data = await objects.get_bytes(key)
    if data is None:
        raise HTTPException(404, not_ready_msg)
    return data


@app.get("/jobs/{job_id}/audio")
async def get_audio(job_id: str):
    job = await meta.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = await _artifact_bytes(job.audio_key, "audio not ready")
    return Response(content=data, media_type=mimetypes.guess_type(job.audio_key)[0] or "application/octet-stream")


@app.get("/jobs/{job_id}/midi")
async def get_midi(job_id: str):
    job = await meta.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = await _artifact_bytes(job.midi_key, "midi not ready")
    return Response(content=data, media_type="audio/midi",
                    headers={"Content-Disposition": f"attachment; filename={job_id}.mid",
                             "ETag": f'"{sha256(data).hexdigest()}"'})


@app.get("/jobs/{job_id}/events")
async def get_events(job_id: str):
    job = await meta.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = await _artifact_bytes(job.events_key, "events not ready")
    return Response(content=data, media_type="application/json")


@app.get("/jobs/{job_id}/midi-parts", response_model=MidiInspection)
async def get_midi_parts(job_id: str):
    """Inspect an existing MIDI artifact without loading or downloading models."""
    job = await meta.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = await _artifact_bytes(job.midi_key, "MIDI not ready")
    try:
        inspected = await asyncio.to_thread(inspect_midi, data)
    except MidiInspectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return inspected.inspection


@app.post("/jobs/{job_id}/tablatures", status_code=202, response_model=TabTask)
async def create_tablature(job_id: str, options: TablatureOptions):
    try:
        return await tabs.submit(job_id, options)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/jobs/{job_id}/tablatures", response_model=list[TabTask])
async def list_tablatures(job_id: str):
    if await meta.get(job_id) is None:
        raise HTTPException(404, "job not found")
    return await tabs.store.list(job_id)


@app.get("/tablatures/{task_id}", response_model=TabTask)
async def get_tablature(task_id: str):
    task = await tabs.store.get(task_id)
    if task is None:
        raise HTTPException(404, "tablature task not found")
    return task


@app.post("/tablatures/{task_id}/retry", status_code=202, response_model=TabTask)
async def retry_tablature(task_id: str):
    try:
        return await tabs.retry(task_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/tablatures/{task_id}/cancel", response_model=TabTask)
async def cancel_tablature(task_id: str):
    try:
        return await tabs.cancel(task_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/tablatures/{task_id}", status_code=204)
async def delete_tablature(task_id: str):
    try:
        await tabs.delete(task_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(status_code=204)


@app.get("/tablatures/{task_id}/{kind}")
async def get_tablature_artifact(task_id: str, kind: str):
    if kind not in ARTIFACTS:
        raise HTTPException(404, "unknown tablature artifact")
    task = await get_tablature(task_id)
    data = await _artifact_bytes(task.artifacts.get(kind), "tablature artifact not ready")
    media = "application/json" if kind in {"json", "report"} else "text/plain; charset=utf-8"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{task_id}-{ARTIFACTS[kind]}"'})
