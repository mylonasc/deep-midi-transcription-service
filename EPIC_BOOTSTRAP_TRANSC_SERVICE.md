# EPIC: Bootstrap Transcription Service (muscriptor + yt-dlp)

## 1. Goal

Build a single-container **FastAPI** backend around **muscriptor** that:

- Accepts a YouTube URL, downloads audio with **yt-dlp**, transcribes to MIDI with `muscriptor`.
- Is **event-driven** with a queue: enqueue songs for download + processing.
- Is **queryable for status** at any stage.
- Supports **downloading, processing, cancelling (at any stage), deleting, retrieving** any pipeline artifact.
- Uses a **SQLite metadata store** + an **object store**, both behind modular interfaces so they can be swapped later (Postgres, S3, ...).
- Takes **per-job configuration** (model size, yt-dlp policy, transcription knobs) with good modularity for future changes.
- Ships as **one container, one volume**; forward-compatible with k8s.

Non-goals: authentication, multi-tenancy, billing, front-end, horizontal scaling in V1.

### Implementation review amendments

Visualization prototype: a lazy-loaded UI workspace tab renders per-track alphaTab
notation and standard guitar/bass fret suggestions from MIDI artifacts. A separate
piano roll consumes original MIDI timings across all instruments (including drums).
MIDI parsing, replaceable fretting and renderer adapters are isolated under
`ui/src/visualization/`. Initial notation is sixteenth-quantized 4/4 with four-bar
pagination; not production engraving, optimal fingering or synchronized playback.

Subsequent additions: manual retry via `POST /jobs/{id}/retry` for failed/cancelled
jobs (same ID/config, persisted retry count, reuse audio, reject active/done with
409); attempt-tagged queue entries prevent duplicate stale work. The dashboard
now uses mobile-first cards, filtering, expanded diagnostics and touch-sized
controls, with Playwright overflow checks at mobile/tablet/desktop widths.
App-level `MUSCRIPTOR_WEIGHTS_SMALL/MEDIUM/LARGE` optionally map job model sizes
to local safetensors plus sibling config.json. Compose mounts a configurable host
HuggingFace hub read-only; `.env.example` documents configuration. These supersede
the earlier default-only weights and UI sketches.

The React UI is now a separate nginx container, with `/api` proxied to FastAPI.
The stack starts using `docker compose up --build`. See `README.md` for current
operational behavior and limitations. These corrections supersede the original
bootstrap sketches below:

- Stage work runs in killable subprocess groups, not uninterruptible threads.
  Cancel/delete wait for the producer to stop; model initialization and FFmpeg
  subprocesses are included. Model instances are loaded per job (disk cache only).
- Progress persists in SQLite during stages. Completed note events persist as
  `jobs/{id}/events.json`; `/events` is not an SSE endpoint. UI status uses polling.
- Playlist fan-out and live streams are rejected explicitly; only one song per
  job is supported. Mix gating is a title heuristic. Unknown duration is rejected.
- Default format is `bestaudio[abr<=128]/worstaudio` (no video fallback).
  Audio is normalized through FFmpeg to PCM WAV before inference because
  libsndfile cannot reliably decode M4A.
- `JobConfig.version` accepts version 1 only. Standard unknown fields are rejected;
  `extensions` is the explicit forward-extension bucket. Numeric options are
  validated; batching above 1 requires `prelude_forcing=false`.
- SQLite uses WAL. Event-artifact schema migration is automatic. Deleted jobs
  return 404 (no tombstone retention / 410 implementation).
- Single replica/process only. Kubernetes horizontal scaling needs durable queue
  claims, not merely a storage adapter or environment-variable change.

## 2. Architecture (bootstrap, single container)

```
Client -> FastAPI (app.py)
            |-- POST /jobs, GET /jobs, GET /jobs/{id}, POST /jobs/{id}/cancel, DELETE /jobs/{id}
            |-- GET /jobs/{id}/audio, /midi, /events
            v
       JobManager (pipeline/manager.py)
            |-- asyncio.Queue + N background workers (same process)
            |-- per-job asyncio.Event for cancellation
            |-- emits JobEvents -> persists to SQLite, updates status
            v
       Downloader (pipeline/downloader.py, yt-dlp) -> ObjectStore.put(audio)
       Transcriber (pipeline/transcriber.py, muscriptor) -> ObjectStore.put(midi)
            v
       MetadataStore (SQLite) + ObjectStore (local FS under /data)
```

Single volume `/data`:

```
/data/
  meta.db            # SQLite
  objects/
    {job_id}/source.m4a   # downloaded audio
    {job_id}/out.mid      # transcribed MIDI
```

## 3. Job lifecycle / state machine

```
queued -> downloading -> downloaded -> transcribing -> done
   |          |             |               |
   +----------+-------------+---------------+-> cancelled
   |          |             |               |
   +----------+-------------+---------------+-> failed
```

- All transitions persisted in SQLite + emitted as events (in-process pub/sub; later Redis/SSE).
- `cancel` is valid in `queued / downloading / downloaded / transcribing`:
  - `queued`: dequeue (lazy skip on pop).
  - `downloading`: abort yt-dlp (kill download task, remove partial file).
  - `transcribing`: signal transcriber to abort between chunks (muscriptor streams chunk-by-chunk, so check cancel event per chunk).
- `DELETE /jobs/{id}`: cancels if active, removes DB row + object keys. Idempotent.
- `GET /jobs/{id}` returns status, stage progress (`download_progress`, `chunks_completed/total`), config snapshot, artifact pointers, error.

## 4. API (FastAPI, no auth)

- `POST /jobs` body: `{ "youtube_url": str, "config": JobConfig | null }` -> `202 { id, status: queued }`
- `GET /jobs?status=&limit=&offset=` -> list
- `GET /jobs/{id}` -> full job incl. config snapshot + progress + error
- `POST /jobs/{id}/cancel` -> `200 { status: cancelled }` (no-op if terminal)
- `DELETE /jobs/{id}` -> `204` (cancel + purge artifacts + row)
- `GET /jobs/{id}/audio` -> audio bytes (if downloaded)
- `GET /jobs/{id}/midi` -> MIDI bytes (if done)
- `GET /jobs/{id}/events` -> transcribed note events (JSON, if available; streamed during transcribing)
- `GET /healthz` -> `{ ok: true }`

All retrieval endpoints return `404` with `{"detail": "artifact not ready"}` if stage not reached, `410` if purged.

## 5. Job-level configuration (modular, versioned)

`JobConfig` is a versioned pydantic model. Unknown fields are **rejected in strict mode off / preserved** — use `model_config = ConfigDict(extra="allow")` on a `extensions: dict` bucket so new knobs don't break old rows. Server merges `job.config ?? server defaults`.

```python
class YtDlpPolicy(BaseModel):
    format: str = "bestaudio[abr<=128]/bestaudio/best"
    max_duration_s: int = 600
    allow_playlist: bool = False
    allow_mix: bool = False       # reject "mix" playlists / "Various Artists - Topic" mixes
    allow_live: bool = False
    rejection_reason: str | None = None

class TranscriptionOptions(BaseModel):
    model: Literal["small","medium","large"] = "medium"
    detect_tempo: Literal["best-effort", True, False] = "best-effort"
    instruments: list[str] | None = None
    batch_size: int | None = None
    beam_size: int = 1

class JobConfig(BaseModel):
    version: int = 1
    yt_dlp: YtDlpPolicy = YtDlpPolicy()
    transcription: TranscriptionOptions = TranscriptionOptions()
    extensions: dict = {}
```

Gate checks (pre-download, in `downloader.py` via yt-dlp `--flat-playlist --print` / `extract_info(process=False)`):

1. `is_playlist and not allow_playlist` -> reject.
2. `title/description matches mix` or `playlist_count > 1 and not allow_mix` -> reject.
3. `duration > max_duration_s` or `is_live and not allow_live` -> reject.
4. Force audio-only format string; never download video.

Transcription knobs map 1:1 to `TranscriptionModel.load_model(weights_path=model)` and `transcribe_and_postprocess(..., detect_tempo, instruments, ...)`.

## 6. Storage modularity

```python
class MetadataStore(Protocol):
    async def create(self, job: Job) -> Job: ...
    async def get(self, id: str) -> Job | None: ...
    async def list(self, ...) -> list[Job]: ...
    async def update_status(self, id: str, status: JobStatus, patch: dict) -> None: ...
    async def delete(self, id: str) -> None: ...

class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes | Path) -> None: ...
    async def get_bytes(self, key: str) -> bytes | None: ...
    async def get_path(self, key: str) -> Path | None: ...
    async def delete_prefix(self, prefix: str) -> None: ...
```

- Bootstrap: `SqliteMetadataStore` (`aiosqlite`, one table `jobs`), `LocalObjectStore` (root `/data/objects`, keys `jobs/{id}/source.*`, `jobs/{id}/out.mid`).
- Swap path: Postgres impl of `MetadataStore`, S3 impl of `ObjectStore`; keys already S3-shaped.

## 7. Event-driven pipeline

- `JobManager.submit()` writes row (`queued`) + `queue.put(id)`.
- Workers (`asyncio.create_task`, `WORKERS=N`, default 1 for GPU/CPU safety) loop on queue, skip if `cancel_requested`.
- Stage functions are cancellable: downloader checks `cancel_event` + yt-dlp progress hook aborts; transcriber checks per-chunk.
- Status query is just a DB read; no coupling to workers. Later: replace queue with Redis Streams, events with SSE/WebSocket — API unchanged.

## 8. Container

Single `Dockerfile` (uv-based, Python 3.14, installs `muscriptor` + `fastapi` + `uvicorn` + `yt-dlp` + `aiosqlite`):

- `CMD ["uvicorn", "transc_service.app:app", "--host", "0.0.0.0", "--port", "8000"]`
- `VOLUME ["/data"]`, `ENV DATA_DIR=/data WORKERS=1 DEFAULT_MODEL=medium`
- k8s path: same image; PVC on `/data`, `ConfigMap` for defaults, later split `Deployment(api)` + `StatefulSet/Jobs(worker)` + Redis when queue outgrows in-process.

## 9. Bootstrap milestones

1. Models + stores + manager skeleton + `POST/GET/cancel/delete/retrieve` (mock downloader/transcriber).
2. Real yt-dlp downloader + gates.
3. Real muscriptor transcriber (lazy `load_model` cache per model size).
4. Dockerfile + smoke test (`POST youtube_url -> poll -> GET midi`).
5. Cancel-at-any-stage + partial-artifact retrieval tests.
