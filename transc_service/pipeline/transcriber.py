"""muscriptor wrapper with isolated, killable model loading and inference."""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from ..models import TranscriptionOptions

def _load(size: str):
    from ..config import ServerConfig
    weights = ServerConfig().weights_for(size)
    from muscriptor import TranscriptionModel
    return TranscriptionModel.load_model(weights)


class TranscribeCancelled(Exception):
    pass


async def transcribe_audio(audio_path: str | Path, options: TranscriptionOptions,
                           cancel: asyncio.Event, progress_cb=None) -> tuple[bytes, list]:
    from .process import run_stage
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        result = await run_stage("transcribe", {"audio": str(audio_path),
            "options": options.model_dump(mode="json")}, Path(directory), cancel, progress_cb)
        return Path(result["midi"]).read_bytes(), json.loads(Path(result["events"]).read_text())


async def _transcribe_audio(audio_path, options, cancel, progress_cb=None):
    """Returns (midi_bytes, events). Checks cancel per yielded event/chunk."""
    from muscriptor.events import ProgressEvent, NoteStartEvent
    # libsndfile cannot reliably decode yt-dlp's AAC/M4A output. Normalize with
    # FFmpeg before handing audio to muscriptor; the process group remains killable.
    normalized = tempfile.TemporaryDirectory()
    wav = Path(normalized.name) / "input.wav"
    def decode():
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(audio_path),
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    await asyncio.to_thread(decode)
    audio_path = wav
    model = await asyncio.to_thread(_load, options.model)

    def gen():
        grid = model.detect_beat_grid_for(audio_path, options.detect_tempo)
        events = []
        for ev in model.transcribe(
            str(audio_path),
            instruments=options.instruments,
            batch_size=options.batch_size,
            beam_size=options.beam_size,
            prelude_forcing=options.prelude_forcing,
        ):
            if cancel.is_set():
                raise TranscribeCancelled("cancelled during transcription")
            events.append(ev)
            if isinstance(ev, ProgressEvent) and progress_cb:
                progress_cb(ev.completed, ev.total)
        if grid is not None:
            grid = grid.with_onset_delay([ev.start_time for ev in events if isinstance(ev, NoteStartEvent)])
        midi = model.events_to_midi_bytes(iter(events), beat_grid=grid)
        return midi, events

    try:
        return await asyncio.to_thread(gen)
    finally:
        normalized.cleanup()


def events_to_jsonable(events: list) -> list[dict]:
    from muscriptor.events import NoteStartEvent, NoteEndEvent
    out = []
    for ev in events:
        if isinstance(ev, NoteStartEvent):
            out.append({"type": "note_start", "pitch": ev.pitch,
                        "start_time": ev.start_time, "instrument": ev.instrument, "index": ev.index})
        elif isinstance(ev, NoteEndEvent):
            out.append({"type": "note_end", "end_time": ev.end_time,
                        "start_event_index": ev.start_event_index})
        # ProgressEvent skipped
    return out
