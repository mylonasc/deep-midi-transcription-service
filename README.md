# Transcription service

FastAPI + SQLite/local object storage, with a separate React UI. No authentication;
run only on a trusted network. Download only content you are authorized to use.

## Run

```sh
docker compose up --build
```

Open http://localhost:3000. API docs: http://localhost:8000/docs. The UI proxies
`/api` to the backend, so it also works from another computer without rebuilding
with a server IP. A named `data` volume contains metadata, artifacts and cached
model downloads. The first transcription downloads weights. The default container
uses CPU; GPU device/runtime configuration is not included. Model inference can
be slow and memory intensive, especially for `large`.

For local development:

```sh
uv pip install -r requirements.txt
DATA_DIR=./data .venv/bin/uvicorn transc_service.app:app
# Another terminal:
cd ui
npm ci
npm run dev
```

FFmpeg and a supported yt-dlp JavaScript runtime (Node) must be installed locally.
The image includes both. YouTube may impose regional, cookie or bot restrictions;
the service reports those failures but does not bypass them.

## Behavior and limitations

### Local model weights

Job-level model selection (`small`, `medium`, `large`) can resolve to local weights
configured by the server:

```sh
cp .env.example .env
# Edit .env:
# MUSCRIPTOR_CACHE_DIR=/home/your-user/.cache/huggingface/hub
# MUSCRIPTOR_WEIGHTS_MEDIUM=/models/huggingface/models--MuScriptor--muscriptor-medium/snapshots/REVISION/model.safetensors
```

Compose mounts the host hub read-only at `/models/huggingface`. Mount the entire
hub so relative snapshot-to-blob symlinks work. Each configured checkpoint must
have a sibling `config.json`. Set `MUSCRIPTOR_WEIGHTS_SMALL` / `_LARGE` similarly,
or leave any size unset to use muscriptor's default HuggingFace cache/download.
An invalid configured path fails explicitly rather than silently downloading.
For native development, export these variables with **host** paths; `.env` is
read by Compose, not automatically by the Python app. Weight paths are app-level
settings, not arbitrary client-supplied job paths. Never commit `.env` or weights.

### Pipeline and UI

- **Visualization tab (prototype):** select a completed MIDI song and view each
  instrument with alphaTab, or switch to an independent all-instrument piano roll.
  Guitar/bass programs get standard-tuning tablature automatically; other pitched
  tracks get standard notation. Format can be overridden. Notation uses four
  4/4 measures per page with sixteenth-note quantization. Frets are heuristic,
  not recovered original fingering or guaranteed playable chords. Out-of-range
  or conflicting notes are reported and remain visible in the original-timing
  piano roll, along with drums. Notation does not yet handle changing meter/tempo,
  percussion scoring; synchronized preview playback is supported through one shared
  Tone.js engine: the piano roll shows a live playhead with follow and click-to-seek,
  while notation auto-pages, shows proportional page position, and seeks by
  page/measure/transport. The alphaTab renderer, MIDI parser,
  and replaceable fret solver live separately in `ui/src/visualization/`.
  [Tayuya](https://github.com/vipul-sharma20/tayuya) was considered but is not used
  in this iteration because it documents solo-only support.

- **Model availability:** the queue form shows per-size file status and refreshes
  every 15 seconds (or manually). `/health/models` checks local/configured files
  and the default HuggingFace cache without downloading or loading models.
  Available means readable, nonempty weights and valid architecture config—not
  a tested inference run. Uncached sizes show "Needs download"; broken configured
  files show "Unavailable". `/healthz` remains independent process liveness.

- **Retry:** `POST /jobs/{id}/retry` (or the Retry button) requeues a failed or
  cancelled job using its original options. It preserves the job ID, increments
  `retry_count`, clears the last error/progress, and reuses completed audio.
  Active or completed jobs return 409; missing jobs return 404. Retries are manual,
  not an automatic infinite loop.
- The mobile-first dashboard uses wrapping cards, status filters, expandable
  diagnostics, and responsive artifact lists. Long URLs/titles/errors stay inside
  the viewport; action controls have 44px minimum touch targets.

- Jobs run automatically after queuing. SQLite is the durable queue source;
  unfinished jobs recover on restart, reusing completed audio downloads.
- A stage runs in a subprocess, making probing, downloading, FFmpeg conversion,
  model loading and inference interruptible. Cancellation waits for the child to
  stop before returning. Deletion then removes metadata and artifacts.
- Live streams and playlist fan-out are explicitly unsupported. A watch URL
  containing a playlist parameter still selects the individual video. Mix
  detection is a title-based heuristic, not a reliable music-content classifier.
- Progress is saved twice per second; the UI polls approximately every two seconds.
  This is live polling, not WebSocket/SSE push. `/events` is the completed note-event
  artifact, not a streaming status endpoint.
- MIDI and note events persist in the object store. Downloaded audio survives a
  transcription failure/cancellation until the job is deleted.
- Each transcription subprocess loads its own model. Weights are cached on disk,
  but model instances are not shared across jobs. Default `WORKERS=1` bounds memory.
- The player uses Tone.js and `@tonejs/midi` with a short scheduling window,
  pause/resume, duration, seeking, velocity and sustained-note restoration. It is
  a synthesized preview, **not** a General MIDI soundfont renderer; instrument
  programs, drums and MIDI controller messages are not faithfully rendered.
- Store protocols separate SQLite/local filesystem implementations from the
  orchestrator. Run only one API process/replica against a volume in this bootstrap
  version. For Kubernetes, use one replica with a persistent volume and a Recreate
  rollout strategy; multiple replicas require a shared queue/claim mechanism and
  suitable metadata/object store backends first.

Per-job configuration is versioned and split into `yt_dlp`, `transcription` and
`extensions`. Unknown standard fields are rejected; `extensions` is reserved for
future plugins. Server defaults merge with explicitly supplied job fields.
See `/docs` for supported options (including `prelude_forcing=false` when batching).

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -v
cd ui
npm run build
npx playwright install chromium
npm test
```

The backend tests use fake stages: they do not contact YouTube or download models.
