# Open-Fret tablature pipeline

Tracking issue: [#7](https://github.com/mylonasc/deep-midi-transcription-service/issues/7).

## Goal and boundaries

Extend YouTube → audio → MIDI with independently resumable MIDI → guitar
tablature. A failed tab task must not invalidate or regenerate the source MIDI.
Existing completed MIDI jobs must be usable. Initial musical scope is one
selected part, standard six-string guitar, capo 0 and frets 0–24. No silent
transposition, note dropping, part merging or whole-band arrangement.

Open-Fret review baseline: `3a62b4cb06ff2900dc1d494b98fb5a3acb174f9f`.
The adapter mirrors its normalized-note inference/chunk stitching with per-window
progress, anchored decoding and sustain-aware correction. Rendering is separate;
no CLI or standalone viewer is imported. The checksum-verified inference source
subset is installed by `tools/install_open_fret.py`. HF model revision is pinned
to `b8edf84f4b76a8bc03bc1d78255c77364fba72eb`. The upstream
code is MIT; training-data/model redistribution rights remain unresolved. Model
installation must be operator-controlled, with no weights bundled by default.

## Indexed implementation slices

“Local” means implemented and verified, not released or deployed to the user's
running service. Git history records publication of the implementation.

| Slice | Issue | Scope | State |
| --- | --- | --- | --- |
| S01 | [#8](https://github.com/mylonasc/deep-midi-transcription-service/issues/8) | MIDI inspection and canonical contracts | Local; regression tests passing |
| S02 | [#9](https://github.com/mylonasc/deep-midi-transcription-service/issues/9) | Pinned inference-only runtime, muscriptor compatibility, real CPU/resource spike | Local; real CPU inference and dependency checks pass |
| S03 | [#10](https://github.com/mylonasc/deep-midi-transcription-service/issues/10) | Inference/correction adapter, validated JSON/AlphaTex/ASCII/report | Local; independent production import gate |
| S04 | [#11](https://github.com/mylonasc/deep-midi-transcription-service/issues/11) | Persisted child tasks and supervised lifecycle | Local; lifecycle/regression coverage |
| S05 | [#12](https://github.com/mylonasc/deep-midi-transcription-service/issues/12) | Task management and artifact API | Local; real API/model/download/deletion smoke passes |
| S06 | [#13](https://github.com/mylonasc/deep-midi-transcription-service/issues/13) | Part selection, generated-tab UI, shared visualization | Local; phone/320px/desktop browser coverage |
| S07 | [#14](https://github.com/mylonasc/deep-midi-transcription-service/issues/14) | Opt-in automatic generation, tempo/meter-aware export | Local; fractional/intra-measure tempo and bar-aligned meter supported |
| S08 | [#15](https://github.com/mylonasc/deep-midi-transcription-service/issues/15) | Model health/configuration, deployment and release hardening | Local; CPU deployment and offline-model defaults |

## S01 implemented contract

`GET /jobs/{id}/midi-parts` inspects the existing artifact regardless of parent
status. It returns 404 for missing jobs/artifacts, 422 for malformed/structurally
unsupported MIDI, and 200 with compatibility diagnostics for inspectable MIDI.
Inspection never loads/downloads models or changes job metadata/artifacts.

- Parts have IDs `t{track}:c{channel}:p{program}`, with zero-based track/channel/
  GM program. These are not frontend track-array indices. Identity is scoped to
  the exact MIDI SHA-256; changed MIDI must be re-inspected before generation.
- Note identity is `t{track}:c{channel}:n{attack_ordinal}`. A note belongs to the
  program at attack even if a program change occurs before release. Equal-pitch
  releases are FIFO within track/channel/pitch. Channel 10 drums are counted and
  excluded. Track names are untrusted plain text, never notation commands.
- Preserve source PPQ/ticks, tempo/meter maps, velocity and original seconds.
  Normalize model ticks to 480 PPQ using exact rational arithmetic and nearest
  integer, ties-to-even. Count distinct rounded source tick values. Never hide
  intervals collapsed by normalization: report a generation blocker.
- Tempo integration includes implicit 120 BPM before the first explicit tempo;
  meter defaults to 4/4. Conflicting simultaneous global tempo/meter events,
  malformed releases, nonpositive note durations, type-2 and SMPTE MIDI fail.
- Pitch bends, sustain pedal, collapsed durations and out-of-range guitar pitches
  are generation blockers. Variable tempo and meter are preserved. Mid-measure
  meter changes and unsupported notation durations fail during export rather than
  being approximated. Non-guitar GM programs are informational warnings, not automatically
  eligible guitar parts. Inspection is not proof that a part is fingerable or
  exactly notatable; inference/correction/export perform those checks.
- Summary responses omit full note arrays; internal normalized data retains
  them. Inspection caps input at 16 MiB / 500,000 decoded messages. Parsing runs
  off the API event loop.

`tablature/models.py` defines strict, versioned initial options and canonical
output. Output references immutable source notes by ID and must contain exactly
one legal, pitch-equivalent assignment per note with no sustained-string overlap.
Standard tuning is concert MIDI `[64,59,55,50,45,40]`, high string first. Canonical
output is separate from model token streams and notation export.

## Implemented task and export architecture

`queued → generating → done/failed/cancelled` child tasks persist in a separate
SQLite JSON table behind `TablatureStore`, alongside the existing job store.
Tasks retain source hash, selected part/options, pinned source/model revisions,
progress, attempt and artifact keys. The explicit `tablature` stage uses the
existing process-group supervisor; its timeout includes load/inference/correction/
Node validation. Tab and transcription stages share `JobManager.compute` permits;
waiting for compute can be cancelled. Restart requeues unfinished tasks. Retries
invalidate stale queue attempts and rerun only tabs. Parent deletion first stops
and removes children; task deletion waits for its producer. Output source notes,
timing, configuration and provenance are rechecked before publishing artifact keys.

Artifacts: `jobs/{job_id}/tablatures/{task_id}/attempt-{n}/tablature.json`, `score.tex`,
`preview.txt` and `report.json`. ASCII is an onset preview, not duration notation.
Keep correction diagnostics distinct from raw-model quality metrics.

API: create/list at `/jobs/{id}/tablatures`, get/delete at
`/tablatures/{id}`, cancel/retry subroutes, and JSON/AlphaTex/ASCII/report downloads.
Never accept arbitrary model IDs, revisions or filesystem paths from clients.

The Visualization workspace's **Generated tablature** view displays stored
fingerings without greedy re-solving, separately from the prototype. Backend part
IDs populate a dedicated picker, never a frontend-track-index lookup. MIDI download
ETags bind the loaded playback source to the inspection/result hash. The original
all-track piano roll and playback engine remain unchanged. Generated excerpts use
the same current/seek API, four-measure tie-preserving pagination and original
tempo-map-based page/measure seconds; the page cursor is proportional, not
note-accurate. Async requests abort on disposal and stale health is cleared.

The queue form's `config.tablature="auto"` submits one eligible guitar part after
MIDI completion, or stores a message asking for manual selection. Startup recovers
the completion→auto-submission crash window. Tab failure does not change the parent
job's success. Model downloads default off. The operator can approve the pinned HF
download or provide a local directory with matching `MODEL_REVISION`; arbitrary
model IDs or filesystem paths never come from job requests.

Muscriptor beat-grid output contains fractional-BPM tempo changes. Its real MIDI
serializer is exercised by the smoke fixture. Tempo is never flattened: AlphaTex
bar metadata uses floating BPM and fractional bar positions, because beat tempo
effects only support integer BPM. Meter changes are accepted at bar boundaries.
The production Node validator independently imports the full score and each
excerpt, compares complete pitch/start/end intervals, and verifies full-score
tempo/meter. Unrepresentable rhythms fail explicitly. Quantization beyond the
reported 480-PPQ rounding, custom tuning/capo, bass, effects and arrangement remain
out of scope.

## Verification and release gates

The ordinary backend suite uses in-memory MIDI and fake inference, never a corpus,
model download or network. Tests cover identities/tempo/rounding/contracts, task
lifecycle, retries/recovery, publication failures, cancellation while waiting for
compute, source/output tampering, automatic submission, APIs and parent deletion.
Playwright tests cover generated-tab retry/download/render/seek/delete/health and
44px targets/overflow on five phones, 320px and desktop, with mocked APIs.

Optional `tools/smoke_tablature.py` runs the actual pinned model over 144 notes,
two inference windows and three pages, with overlap, page-spanning sustains,
intra-measure tempo and bar-aligned meter changes. `--muscriptor` uses muscriptor's
actual variable-beat-grid MIDI serializer and a second excluded pitched part.
`tools/smoke_tablature_api.py` exercises real API→subprocess→validation→SQLite/
object artifacts→downloads→parent deletion. All use synthetic notes, not audio
inference or YouTube. `tools/verify_tablature.cjs` invokes the production gate.

Recorded native CPU baseline: Linux/Python 3.12, Torch/torchaudio 2.11.0+cpu,
Transformers 5.22.2, two OMP threads, pinned alphaTab 1.9.0. Combined requirements
resolve and `uv pip check` passes; muscriptor imports and serializes MIDI. Initial
download/cold smoke: ~10.4 seconds / 485 MiB peak RSS. Cached smoke including Node
validation: ~3 seconds / 442 MiB. These are small-fixture observations, not song
throughput or memory guarantees. Docker installs the same ABI-matched CPU wheels,
verified sources and validator, not weights. CUDA and live YouTube/audio inference
are not validated by these tests. No model-quality or biomechanical guarantee is
implied; upstream model/data licensing remains unresolved.

Final recorded checks: **51 backend tests**, **28 Playwright tests**, frontend
TypeScript/Vite build, `uv pip check`, Docker CPU image build and Compose config
validation pass. The real API smoke and muscriptor-serializer fixture also pass
inside the built container with `--network none` and cached model weights mounted
read-only. Lint exits successfully with React effect/state advisory warnings
(including existing preview components). No running deployment was replaced and
the running service remains unchanged by publishing the implementation.
