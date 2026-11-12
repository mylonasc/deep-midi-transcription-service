# Architecture and contributor guide

## Maintain this document

**Update `AGENTS.md` whenever significant changes are made** to the architecture,
pipeline lifecycle, API, configuration, storage interfaces, deployment, or testing
workflow. Keep this document aligned with the implementation; update `README.md`
and the EPIC specification when their descriptions are affected too.

## Overview

This service accepts a YouTube URL, queues an audio download using yt-dlp, and
transcribes the audio into MIDI using muscriptor. FastAPI serves job management
and artifact retrieval. A separate React application provides a dashboard and
browser MIDI playback. Authentication is intentionally out of scope; deploy on
a trusted network.

Docker Compose runs two containers:

- **`api`**: FastAPI and in-process queue orchestration. Download and transcription
  stages execute in isolated subprocess groups.
- **`ui`**: nginx serving the built React application and proxying `/api/` to
  `http://api:8000/`. Browser requests use the same origin.

The API's `/data` volume stores SQLite metadata, object-store artifacts, and model
download caches. The bootstrap deployment supports **one API process/replica**;
`WORKERS` controls pipeline concurrency within that process, not ASGI workers.

## Backend layout

- `transc_service/app.py`: FastAPI routes, application lifespan, URL validation,
  server-default/per-job configuration merging, and dependency assembly.
- `transc_service/models.py`: Pydantic job, status, progress, and versioned
  configuration models. Standard unknown fields are rejected. `extensions` is
  reserved for future configuration extensions.
- `transc_service/config.py`: environment-based server settings, including
  `DATA_DIR`, `WORKERS`, `DEFAULT_MODEL`, and `MAX_DURATION_S`. Optional
  `MUSCRIPTOR_WEIGHTS_SMALL`, `MUSCRIPTOR_WEIGHTS_MEDIUM`, and
  `MUSCRIPTOR_WEIGHTS_LARGE` map job-selected model sizes to server-owned local
  weights; the checkpoint must have sibling `config.json`. Unset paths retain
  muscriptor's normal download/cache behavior. Do not accept filesystem paths
  from untrusted job requests.
- `transc_service/model_health.py`: offline model-file availability checks for
  each size. Checks configured checkpoints or the default HuggingFace cache for
  readable nonempty weights and valid architecture configuration. Never downloads
  or loads models; file availability is not proof of successful inference.
- `transc_service/tablature/midi.py` and `models.py`: model-free MIDI inspection,
  source-note identity/timing normalization and strict canonical tablature
  contracts. Parts use track/channel/program IDs scoped to the source MIDI hash,
  never frontend track-array indices. Normalize to 480 PPQ with explicit rounding
  diagnostics; preserve original ticks/seconds and tempo/meter maps. Inspection
  reports compatibility blockers, not proof of fingerability.
- `transc_service/tablature/adapter.py`: pinned, deterministic Open-Fret anchored
  decoding and sustained-string correction in a killable subprocess. Chunking
  mirrors upstream's normalized-note inference with per-window progress. Source
  revision and model commit are pinned; local snapshots need `MODEL_REVISION`.
- `transc_service/tablature/export.py` and `durations.py`: renderer-independent
  exact interval/tempo/meter AlphaTex, tie-preserving four-measure excerpts and
  labelled ASCII onset previews. `tools/notation/verify.cjs` independently imports
  every full score and excerpt before publication, comparing pitches, attacks,
  releases and full-score tempo/meter. Tempo changes use floating BPM bar metadata
  with explicit fractional bar positions (beat effects only accept integer BPM).
  Mid-measure meter changes and unrepresentable rhythms fail.
- `transc_service/tablature/tasks.py`, `store.py`: separate `TablatureStore`
  protocol, SQLite JSON child tasks, attempt-aware queue/recovery, retries,
  cancellation/deletion and artifact publication. Share `JobManager.compute`
  permits with transcription; waiting for a permit is cancellable. Parent deletion
  uses `before_delete` to stop/remove child tasks before parent producers/artifacts.
  `health.py` performs offline file/runtime checks, never inference/downloads.
  Indexed implementation slices are in `EPIC_TABLATURE.md` and GitHub issue #7.
- `transc_service/pipeline/manager.py`: queue, recovery, worker lifecycle, progress
  persistence, cancellation, deletion, and artifact publication. Depends on store
  protocols rather than concrete backends.
- `transc_service/pipeline/process.py`: stage subprocess supervision, progress
  message handling, and process-group termination on cancellation.
- `transc_service/pipeline/stage_worker.py`: private subprocess entry point using
  JSON requests and prefixed JSON stdout messages for progress/results.
  Stdout is reserved exclusively for the protocol; third-party output is redirected
  to stderr. The parent drains stderr concurrently and retains bounded diagnostics.
  Never merge stderr into stdout: carriage-return progress bars can hide results.
- `transc_service/pipeline/downloader.py`: yt-dlp metadata probing, download
  policy gates, and audio download/extraction.
- `transc_service/pipeline/transcriber.py`: FFmpeg normalization to mono 16 kHz
  PCM WAV, muscriptor model loading/inference, MIDI serialization, and note-event
  serialization. Models load per job; downloaded weights are cached on disk.

### Lifecycle and cancellation

Normal flow:

```text
queued -> downloading -> downloaded -> transcribing -> done
```

Nonterminal jobs may fail or be cancelled. SQLite is the durable source of job
state; the `asyncio.Queue` is an in-process work scheduler. On startup, unfinished
jobs are requeued, reusing completed audio downloads when available. Progress is
persisted approximately every half second. Graceful shutdown stops active stages.

Cancellation must stop stage producers, including model loading and FFmpeg,
before returning. Deletion waits for cancellation before removing artifacts and
metadata, preventing producers from recreating deleted files. Preserve this
ordering when changing orchestration.

`POST /jobs/{id}/retry` requeues failed/cancelled jobs in place using their original
configuration and existing audio, clears the error/progress, and increments
persisted `retry_count`. Active/completed jobs return 409. Queue messages include
an attempt token so stale entries from cancelled attempts cannot duplicate work.
Concurrent retries are serialized within the single API process.

Tab tasks have an independent `queued -> generating -> done/failed/cancelled`
lifecycle. Task options reference a MIDI SHA-256 and stable part ID; compare
source notes/timing, options and pinned model provenance again before publishing
artifact keys. Attempts write under distinct `attempt-{n}` prefixes. Partial
uploads have no published keys and are removed on task/parent deletion. Successful
MIDI transcription never fails because tabs fail. `config.tablature="auto"`
optionally submits one unambiguous eligible guitar part; persisted
`auto_tablature_message` explains ambiguity/submission. Startup recovers missing
auto submission after MIDI completion. Automatic task creation is idempotent
within the single API process; tab retries never rerun audio transcription.

Each job represents **one song**. Playlist fan-out and live streams are currently
unsupported. Duration must be known and within the job's limit. Mix detection is
a metadata/title heuristic, not content classification. Batched transcription
above one requires `prelude_forcing=false`.

### API

- `POST /jobs`: validate configuration, persist and enqueue a job.
- `GET /jobs`, `GET /jobs/{id}`: list/query status and artifact keys.
- `POST /jobs/{id}/cancel`: stop queued or active work.
- `POST /jobs/{id}/retry`: retry failed/cancelled jobs (202); reject other states (409).
- `DELETE /jobs/{id}`: stop work and remove the job and its artifacts.
- `GET /jobs/{id}/audio`, `/midi`, `/events`: retrieve completed stage artifacts.
- `GET /jobs/{id}/midi-parts`: model-free inspection of an existing MIDI artifact,
  regardless of job status. Returns part summaries, source hash, original PPQ,
  tempo/meter maps, and compatibility/rounding diagnostics. Missing job/artifact
  is 404; malformed or structurally unsupported MIDI is 422. Compatibility/tempo/
  meter diagnostics are reported with 200 for inspectable input.
  Input is bounded at 16 MiB / 500,000 decoded messages; parsing runs in a thread.
- `POST/GET /jobs/{id}/tablatures`: create (202) / list child tasks. Creation
  rejects incompatible input or stale source hashes with 422, missing jobs 404.
- `GET/DELETE /tablatures/{id}` and `POST /tablatures/{id}/cancel`, `/retry`:
  query/remove/stop/retry tasks; retry active/completed tasks returns 409.
- `GET /tablatures/{id}/{json,alphatex,ascii,report}`: completed task downloads;
  missing/unpublished artifacts return 404, independent of the parent status.
- `GET /health/tablature`: offline runtime/validator/pinned-model availability;
  downloads are operator-opt-in. File availability is not successful inference.
- `GET /healthz`: liveness endpoint; `/docs` exposes the API schema.
- `GET /health/models`: per-size `available`, `download_required`, or `unavailable`
  plus source/message. Returns 200 for a completed check even if models are missing;
  model-file status is independent of process liveness.

`/events` returns a completed note-event JSON artifact, **not** an SSE stream.
The dashboard uses polling for status. Missing jobs/artifacts return 404.

## Storage boundaries

- `transc_service/stores/__init__.py` defines `MetadataStore` and `ObjectStore`
  protocols. Keep orchestration independent of SQLite/local-file details.
- `transc_service/stores/sqlite.py` implements metadata persistence with
  aiosqlite, WAL mode, and initialization-time schema migration.
- `transc_service/stores/local.py` implements a local filesystem object store,
  with traversal checks on object keys.

Artifacts use keys under `jobs/{id}/`: downloaded `source.*`, `out.mid`, and
`events.json`. Tab artifacts use `jobs/{id}/tablatures/{task}/attempt-{n}/` with
`tablature.json`, `score.tex`, `preview.txt` and `report.json`. Canonical JSON also
contains validated notation excerpts and original source identities/timing.
Artifact availability is determined by stored keys, not only job
status: audio can remain available after transcription failure/cancellation.
New backends must preserve these semantics and the protocol contracts.

## Frontend

`ui/` uses React, TypeScript, and Vite:

- `ui/src/lib/api.ts`: typed API client, relative `/api` base URL, and job-list
  pagination.
- `ui/src/app/SiteHeader.tsx` and `WorkspaceTabs.tsx`: header/connection indicator
  and tab switcher (arrow-key nav). No data logic.
- `ui/src/components/ui/Notice.tsx` and `EmptyState.tsx`: shared alert banners and
  placeholder panels.
- `ui/src/features/jobs/QueueForm.tsx` and `JobsSection.tsx`: queue form and
  summary/filter/cards. `ui/src/features/artifacts/ArtifactLibrary.tsx`: grouped
  artifact lists.
- `ui/src/lib/useJobPolling.ts`: 2s job-list polling with disposal.
- `ui/src/styles/tokens.css`: spacing/type/radius/safe-area tokens imported by
  `index.css`, which also carries the sticky mobile transport, skeleton shimmer
  (disabled under reduced motion), and safe-area padding.
- `ui/src/components/JobCard.tsx`: mobile-first job cards, progress, expandable
  error diagnostics, retry/cancel/delete actions, and per-job pending state.
- `ui/src/features/artifacts/ArtifactLibrary.tsx`: responsive queued/audio/MIDI artifact lists.
- `ui/src/components/ModelHealth.tsx`: model availability next to model selection,
  refreshed every 15 seconds or manually. Failed checks must not show stale green
  availability indicators; downloads-on-first-use are distinct from local failures.
- `ui/src/index.css`: mobile-first layout with wrapping cards and grid tracks,
  minimum 44px action targets, and visible keyboard focus. Avoid fixed-width
  tables or hiding page overflow to conceal layout bugs.
- `ui/src/components/MidiPlayer.tsx`: parses MIDI with `@tonejs/midi`, synthesizes
  notes with Tone.js, and supports play/pause/restart and timeline seeking.
  Uses short-lookahead scheduling rather than scheduling the entire score at once.
- `ui/vite.config.ts`: local-development `/api` proxy to port 8000.
- `ui/nginx.conf`: production same-origin API proxy and SPA fallback.

The MIDI player is a synthesized preview, not a faithful General MIDI renderer:
instrument programs, percussion, and controller messages are not fully rendered.
Preserve user-gesture audio activation, async-load cleanup, sustained-note
restoration on seek, and disposal of scheduled audio when changing jobs.

### Visualization prototype (separate workspace tab)

The **Visualization** workspace is lazy-loaded and reads existing MIDI artifacts;
no backend transcription or storage changes are needed. Keep these layers separate:

- `ui/src/visualization/types.ts` and `midi.ts`: normalized, renderer-neutral MIDI
  tracks/notes, original timing, instrument programs and percussion flags.
- `TablaturePanel.tsx`: backend part selection, independent task polling/actions,
  model health, downloads and stored Open-Fret AlphaTex excerpts. Never re-solve
  these assignments with the prototype greedy resolver or equate backend part IDs
  with frontend track indices. Generated tabs use the existing shared transport
  and original MIDI piano roll; seeking/auto-follow use persisted tempo-aware page
  and measure seconds. Poll/load/action fetches are aborted on disposal; failed
  health refresh clears old availability. Generated tabs are explicitly separate
  from the existing prototype notation view.
- `fretting.ts`: replaceable `FretResolver` interface and a fast greedy resolver.
  It assigns standard guitar/bass strings and 0–24 frets, preserves pitch, and
  avoids simultaneous occupancy of a string. It is independent of alphaTab and
  does not guarantee optimal or physically playable fingering. Unassignable
  notes are counted, not transposed. Tayuya was evaluated but not integrated:
  its documented solo-only limitation does not cover these polyphonic tracks.
- `alphaTex.ts`: notation adapter, quantization, excerpt pagination and alphaTex
  generation; accepts an injected fret resolver. Titles must be escaped so MIDI
  metadata cannot become alphaTex commands.
- `AlphaTabView.tsx`: alphaTab rendering/lifecycle only, plus a proportional
  page-cursor overlay driven by the shared playback position. Renderer and fret solver
  must remain independently replaceable. Bravura font and its OFL license are
  bundled locally through Vite; no external font/soundfont download is needed.
- `PianoRoll.tsx`: independent original-timing piano roll. Defaults to all tracks,
  includes drums and notes omitted from fretting, and has its own filter/time
  window. Shows a live playhead, highlights sounding notes, follows playback when
  enabled, and click-to-seek calls back into the shared engine. Do not derive it from quantized notation or fret-assignment results.
- `playback.ts` + `useMidiPlayback.ts`: renderer-neutral page/measure time helpers
  and one shared Tone.js preview engine (short-lookahead scheduling, user-gesture
  start, sustained-note restoration, disposal on song change). Both views consume the
  same position/seek API so roll and tabs stay synchronized.
- `Transport.tsx`, `PageNavigator.tsx`, `useVisualizationState.ts`: shared playback
  transport, page/measure seeking, and notation-view state (selection, auto-follow,
  cursor fraction). `VisualizationWorkspace.tsx` stays a thin composition over song
  selection, cancellable MIDI loading with skeleton feedback, and these parts.

Prototype limitations: notation assumes 4/4 and quantizes to sixteenths, four
measures per page; meter/tempo changes, expressive effects and percussion notation
are not represented. Ties preserve quantized sustained durations within an
excerpt; each excerpt starts afresh. Piano roll keeps the original timing.
A shared Tone.js preview plays both views in sync: the roll shows a playhead with
optional follow and click-to-seek, while notation auto-pages, shows proportional
page position, and seeks by page/measure/transport. That page cursor is
proportional, not note-accurate, and page boundaries use average ticks-to-seconds
mapping under tempo changes. Wide notation/roll content may
scroll within their own containers, never force horizontal page overflow.
Playwright tests exercise real alphaTab SVG rendering on mobile/desktop, alphaTex
parsing, fret assignment invariants, and independent all-instrument piano-roll data.
`tests/devices.ts` + `tests/e2e-journey.spec.ts` run the full queue → artifacts →
preview → synchronized-visualization journey on five phones (iPhone SE/14/14 Pro
Max, Pixel 7, Galaxy S22) plus a desktop control, asserting layout overflow,
44px targets, and labelled controls per device.

## Running and testing

```sh
docker compose up --build
# UI: http://localhost:3000 ; API docs: http://localhost:8000/docs

# Backend regression tests (no YouTube access or model downloads):
.venv/bin/python -m unittest discover -s tests -v

# Frontend checks:
cd ui
npm ci
npm run build
npx playwright install chromium
npm test
# Alternatively use installed Chrome:
# CHROME_BIN=/usr/bin/google-chrome npm test
```

Backend tests cover lifecycle, progress persistence, cancellation/deletion,
artifact persistence, configuration validation, URL validation, and subprocess
termination. `tests/test_tablature.py` additionally generates in-memory MIDI to
check part/note identities, tempo/meter preservation, rational tick normalization,
compatibility diagnostics, inspection API/artifact retention and canonical
fingering pitch/assignment/sustained-occupancy invariants, without model downloads.
`test_tablature_tasks.py` covers durable independent tasks, stale source/output
rejection, publication failures, recovery, retries, shared-compute cancellation,
automatic submission, task API and parent deletion. Optional `tools/smoke_tablature.py`
and `smoke_tablature_api.py` use real pinned inference; `--muscriptor` exercises
muscriptor's actual beat-grid MIDI serializer without audio inference.
`tools/verify_tablature.cjs` invokes the same independent production alphaTab gate.
`ui/tests/tablature.spec.ts` covers generated-tab failures/retries/downloads,
rendering/shared seeking, stale health, deletion, touch targets and overflow on
five phones, 320px and desktop. These browser tests mock API inference.
Playwright tests use mocked API responses and generated MIDI to
exercise artifact retention and browser playback/seek behavior.
Responsive Playwright cases cover 320/375/768/1280px widths with long unbroken
titles, URLs and expanded diagnostics, viewport/content overflow, control labels,
44px touch targets, filtering, retry interactions, and error states. These tests
mock the API; they do not prove live YouTube/model integration.

FFmpeg and Node are needed by actual pipeline stages and are installed in the
backend image. Model downloads and YouTube restrictions make live integration
tests separate from deterministic regression tests. Do not commit `.venv/`,
downloaded audio, generated MIDI, model caches, runtime data, or secrets.

Compose reads `.env` (untracked) for weight paths; `.env.example` documents the
options. `MUSCRIPTOR_CACHE_DIR` is the host HuggingFace **hub** directory mounted
read-only at `/models/huggingface`. Mount the complete hub, not a snapshot alone:
snapshot weights can be relative symlinks into `blobs/`. Application weight paths
must refer to container paths under that mount when using Compose. Native local
development must export host paths instead; the service does not auto-load `.env`.

Docker includes the checksum-verified MIT Open-Fret inference source subset and
alphaTab validator, not weights/corpus/training utilities. `requirements-tablature.txt`
pins Transformers/tokenizer/Hub plus ABI-matched Torch/torchaudio 2.11.0; the image
installs CPU wheels first. `OPEN_FRET_ALLOW_DOWNLOAD` defaults false, model commit
`b8edf84f4b76a8bc03bc1d78255c77364fba72eb`; optional `OPEN_FRET_MODEL_DIR`
must contain matching `MODEL_REVISION`. Device defaults CPU; native CUDA requires
an operator-provided matching Torch/torchaudio environment. `OPEN_FRET_TIMEOUT_S`
(300) bounds the entire inference subprocess; `OPEN_FRET_MAX_NOTES` (20,000) bounds
selected-part input. The pinned corrector also has a bounded search. Model/data
redistribution rights remain unresolved upstream; see `THIRD_PARTY_NOTICES.md`.

## Deployment evolution

For initial Kubernetes deployment, use one API replica with persistent `/data`
storage and a Recreate rollout strategy. Multiple replicas require a shared work
queue/claim mechanism and appropriate metadata/object store backends; merely
adding replicas or swapping storage adapters is insufficient. Keep per-job
configuration and storage modular as these capabilities evolve.
