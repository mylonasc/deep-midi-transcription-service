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
uv venv
uv pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install -r requirements.txt -r requirements-tablature.txt
.venv/bin/python tools/install_open_fret.py "$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
npm ci --prefix tools/notation
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

- **Open-Fret guitar tablature:** in **Visualization → Generated tablature**, select
  a MIDI part and generate persistent model-assisted fingerings. This is separate
  from the heuristic prototype below. Preview uses the existing MIDI transport;
  download JSON, AlphaTex, ASCII onset preview and diagnostics. Tasks have their
  own progress, cancellation, retries and deletion. Tab failure never invalidates
  MIDI. Existing MIDI jobs are supported. The queue form can opt into automatic
  tabs when exactly one eligible guitar part exists; ambiguous cases require
  manual selection. See [implementation slices and contracts](EPIC_TABLATURE.md).

  **Operator setup:** weights are not bundled and downloads default to disabled.
  After reviewing upstream's unresolved training-data/model redistribution rights,
  set `OPEN_FRET_ALLOW_DOWNLOAD=true` in Compose's `.env` to download the pinned
  model on first use. Alternatively set `OPEN_FRET_MODEL_DIR` to a container-visible
  local snapshot with a `MODEL_REVISION` text file containing the configured HF
  commit. `.env.example` documents paths, revision, timeout and note limits.
  Native development must export these variables instead. Model health is at
  `/health/tablature`; a failed check never affects MIDI-only work or `/healthz`.

  Initial scope: one selected part, six strings in E standard, capo 0, frets 0–24.
  No transposition, dropped notes or full-band arrangement. Sustain pedal, pitch
  bends, malformed notes and out-of-range pitches fail explicitly. Tempo changes
  (including within measures), independent sustains and meter changes at bar
  boundaries are preserved; mid-measure meter changes and unrepresentable rhythms
  fail rather than silently quantize. Every production export is independently
  imported by alphaTab and checked for complete pitches/attacks/releases and
  tempo/meter. 480-PPQ tick rounding is reported; original ticks/seconds remain in
  canonical JSON. Feasible fingering is not a biomechanical playability guarantee.

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

Per-job configuration is versioned and split into `yt_dlp`, `transcription`,
optional `tablature: "off" | "auto"` (default `"off"`), and
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
The optional pinned-model integration checks are separate:

```sh
# Requires the inference runtime and tools/notation dependencies installed above.
# Use a NEW output directory. Downloads require explicit operator opt-in.
OPEN_FRET_ALLOW_DOWNLOAD=true OMP_NUM_THREADS=2 .venv/bin/python tools/smoke_tablature.py /tmp/new-tab-smoke
node tools/verify_tablature.cjs /tmp/new-tab-smoke
# Exercise muscriptor's real MIDI serializer (no audio inference):
OPEN_FRET_ALLOW_DOWNLOAD=true OMP_NUM_THREADS=2 .venv/bin/python tools/smoke_tablature.py /tmp/new-muscriptor-smoke --muscriptor
# Full API/store/subprocess/download/deletion path, temporary SQLite and artifacts:
OPEN_FRET_ALLOW_DOWNLOAD=true OMP_NUM_THREADS=2 .venv/bin/python tools/smoke_tablature_api.py
```

These fixtures test pipeline correctness, not held-out model quality or live
YouTube/audio transcription. See `THIRD_PARTY_NOTICES.md` for inference/duration
code attribution and the distinct model/data rights caveat.
