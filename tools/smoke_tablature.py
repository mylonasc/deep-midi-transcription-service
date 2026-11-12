"""Opt-in real pinned-model smoke test. Never run by unittest discovery.

OPEN_FRET_ALLOW_DOWNLOAD=true HF_HOME=/path/to/cache python tools/smoke_tablature.py /new/output
Run tools/verify_tablature.cjs separately with UI dependencies installed.
"""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mido

from transc_service.tablature.adapter import run_tablature
from transc_service.tablature.midi import inspect_midi
from transc_service.tablature.models import TablatureOptions, TablatureResult


def fixture():
    midi = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.MetaMessage("set_tempo", tempo=600000, time=240),
        mido.MetaMessage("set_tempo", tempo=750000, time=1680),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=5760),
    ])
    track = mido.MidiTrack([mido.Message("program_change", program=24)])
    # >512 input tokens: exercise real chunk stitching plus page boundaries.
    events = []
    for i in range(144):
        start = 120 + i * 120
        # Alternate overlapping strings, including independently released chords.
        pitch = 64 if i % 2 == 0 else 59
        events.extend([(start, mido.Message("note_on", note=pitch, velocity=80)),
                       (start + 180, mido.Message("note_off", note=pitch))])
    previous = 0
    for tick, message in sorted(events, key=lambda item: (item[0], item[1].type == "note_on")):
        message.time, previous = tick - previous, tick
        track.append(message)
    midi.tracks.extend([meta, track])
    output = BytesIO()
    midi.save(file=output)
    return output.getvalue()


def muscriptor_fixture():
    """Exercise muscriptor's actual MIDI serializer, not an audio/model mock."""
    import numpy as np
    from muscriptor import TranscriptionModel
    from muscriptor.events import NoteStartEvent, NoteEndEvent
    from muscriptor.utils.beats import BeatGrid
    model = object.__new__(TranscriptionModel)
    model._inst_to_program = {"guitar": 24, "piano": 0}
    events = []
    for index in range(144):
        attack = NoteStartEvent(pitch=64 if index % 2 == 0 else 59, start_time=0.125 + index * 0.125,
                                index=index, instrument="guitar")
        events.extend([attack, NoteEndEvent(end_time=attack.start_time + 0.1875, start_event=attack)])
    piano = NoteStartEvent(pitch=60, start_time=0, index=200, instrument="piano")
    events.extend([piano, NoteEndEvent(end_time=1, start_event=piano)])
    grid = BeatGrid(bpm=120, beats_per_bar=4, first_downbeat=0,
                    beats=np.array([i * 0.5 for i in range(17)] + [8 + i * 0.6 for i in range(1, 22)]), steady=False)
    return model.events_to_midi_bytes(iter(events), beat_grid=grid)


async def main():
    destination = Path(sys.argv[1])
    if destination.exists():
        raise ValueError("Use a new output directory; smoke artifacts are never overwritten")
    data = muscriptor_fixture() if "--muscriptor" in sys.argv else fixture()
    summary = inspect_midi(data).inspection
    options = TablatureOptions(part_id=next(part.id for part in summary.parts if part.guitar_program),
                               source_midi_sha256=summary.source_midi_sha256)
    artifacts = await run_tablature(data, options, asyncio.Event(), lambda *values: print(values, flush=True))
    result = TablatureResult.model_validate_json(artifacts["tablature.json"])
    assert len(result.source_notes) == len(result.assignments) == 144
    assert len(result.notation_pages) >= 2
    report = json.loads(artifacts["report.json"])
    assert len(report["windows"]) > 1
    destination.mkdir(parents=True)
    for name, content in artifacts.items():
        (destination / name).write_bytes(content)
    (destination / "source.mid").write_bytes(data)
    print(json.dumps({"notes": len(result.assignments), "pages": len(result.notation_pages),
                      "windows": len(report["windows"]), "elapsed_seconds": report["elapsed_seconds"]}))


if __name__ == "__main__":
    asyncio.run(main())
