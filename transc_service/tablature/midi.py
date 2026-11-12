"""Inspect MIDI without loading Open-Fret; retain original timing and identities."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from io import BytesIO

import mido

from .models import Diagnostic, MeterChange, MidiInspection, MidiPart, SourceNote, TempoChange

MAX_MIDI_BYTES = 16 * 1024 * 1024
MAX_MIDI_MESSAGES = 500_000


class MidiInspectionError(ValueError):
    """Malformed or structurally unsupported MIDI; safe to expose to the API."""


@dataclass
class InspectedMidi:
    inspection: MidiInspection
    notes_by_part: dict[str, list[SourceNote]]


def inspect_midi(data: bytes) -> InspectedMidi:
    if len(data) > MAX_MIDI_BYTES:
        raise MidiInspectionError("MIDI exceeds the 16 MiB inspection limit")
    try:
        return _inspect(data)
    except MidiInspectionError:
        raise
    except (EOFError, OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise MidiInspectionError("Malformed MIDI data") from exc


def _inspect(data: bytes) -> InspectedMidi:
    midi = mido.MidiFile(file=BytesIO(data))
    if midi.type == 2:
        raise MidiInspectionError("Asynchronous type-2 MIDI is unsupported")
    if midi.ticks_per_beat <= 0:
        raise MidiInspectionError("SMPTE time division is unsupported; PPQ MIDI is required")

    ppq = midi.ticks_per_beat
    scale = Fraction(480, ppq)
    rounded = set()

    def normalize(tick):
        value = tick * scale
        if value.denominator != 1:
            rounded.add(tick)
        return round(value)

    # Defaults are replaced by explicit events at tick zero.
    tempi, meters = {}, {}
    groups, names = {}, {}
    features = defaultdict(set)
    drums = messages = 0
    for track_index, track in enumerate(midi.tracks):
        tick, ordinal = 0, 0
        programs = defaultdict(int)
        active = defaultdict(deque)
        for msg in track:
            messages += 1
            if messages > MAX_MIDI_MESSAGES:
                raise MidiInspectionError("MIDI exceeds the 500000-message inspection limit")
            tick += msg.time
            if msg.type == "track_name":
                names.setdefault(track_index, msg.name)
            elif msg.type in {"set_tempo", "time_signature"}:
                mapping = tempi if msg.type == "set_tempo" else meters
                value = msg.tempo if msg.type == "set_tempo" else (msg.numerator, msg.denominator)
                if tick in mapping and mapping[tick] != value:
                    raise MidiInspectionError(f"Conflicting {msg.type} events at tick {tick}")
                if msg.type == "set_tempo" and value <= 0:
                    raise MidiInspectionError("MIDI tempo must be positive")
                mapping[tick] = value
            elif msg.type == "program_change":
                programs[msg.channel] = msg.program
            elif msg.type == "control_change" and msg.control == 64 and msg.value >= 64 and msg.channel != 9:
                features[track_index, msg.channel].add("sustain_pedal")
            elif msg.type == "pitchwheel" and msg.pitch and msg.channel != 9:
                features[track_index, msg.channel].add("pitch_bend")
            elif msg.type in {"note_on", "note_off"}:
                attack = msg.type == "note_on" and msg.velocity > 0
                if msg.channel == 9:
                    drums += bool(attack)
                    continue
                if attack:
                    key = (track_index, msg.channel, programs[msg.channel])
                    note = {"id": f"t{track_index}:c{msg.channel}:n{ordinal}",
                            "pitch": msg.note, "velocity": msg.velocity,
                            "source_start_tick": tick, "source_end_tick": None}
                    ordinal += 1
                    groups.setdefault(key, []).append(note)
                    active[msg.channel, msg.note].append(note)
                else:
                    queue = active[msg.channel, msg.note]
                    if not queue:
                        raise MidiInspectionError(f"Unmatched note-off in track {track_index}, channel {msg.channel}")
                    note = queue.popleft()
                    if tick <= note["source_start_tick"]:
                        raise MidiInspectionError("MIDI notes must have positive duration")
                    note["source_end_tick"] = tick
        if any(active.values()):
            raise MidiInspectionError(f"Unterminated notes in track {track_index}")

    tempi.setdefault(0, 500_000)
    meters.setdefault(0, (4, 4))
    tempo_ticks = sorted(tempi)
    elapsed = [Fraction(0)]
    for previous, current in zip(tempo_ticks, tempo_ticks[1:]):
        elapsed.append(elapsed[-1] + Fraction((current - previous) * tempi[previous], ppq * 1_000_000))

    def seconds(tick):
        i = bisect_right(tempo_ticks, tick) - 1
        return float(elapsed[i] + Fraction((tick - tempo_ticks[i]) * tempi[tempo_ticks[i]], ppq * 1_000_000))

    tempo_map = [TempoChange(source_tick=t, tick=normalize(t), time_seconds=seconds(t),
                            microseconds_per_beat=tempi[t]) for t in tempo_ticks]
    meter_map = [MeterChange(source_tick=t, tick=normalize(t), numerator=meters[t][0],
                            denominator=meters[t][1]) for t in sorted(meters)]
    diagnostics = []
    if len(set(tempi.values())) > 1:
        diagnostics.append(Diagnostic(code="variable_tempo",
            message="Tempo changes are preserved in generated notation and playback mapping."))
    if set(meters.values()) != {(4, 4)}:
        diagnostics.append(Diagnostic(code="meter_changes",
            message="Meter is preserved; changes inside a measure cannot be exported."))

    parts, notes_by_part = [], {}
    for (track, channel, program), raw_notes in sorted(groups.items()):
        part_id = f"t{track}:c{channel}:p{program}"
        notes = [SourceNote(**note, start_tick=normalize(note["source_start_tick"]),
                            end_tick=normalize(note["source_end_tick"]),
                            start_seconds=seconds(note["source_start_tick"]),
                            end_seconds=seconds(note["source_end_tick"])) for note in raw_notes]
        notes.sort(key=lambda note: (note.start_tick, note.pitch, note.id))
        notes_by_part[part_id] = notes
        warnings = []
        for code in sorted(features[track, channel]):
            warnings.append(Diagnostic(code=code, blocks_generation=True,
                message={"sustain_pedal": "Pedal-aware note ends are not supported.",
                         "pitch_bend": "Pitch bends cannot be represented by fixed-pitch fingerings."}[code]))
        if any(note.start_tick == note.end_tick for note in notes):
            warnings.append(Diagnostic(code="collapsed_duration", blocks_generation=True,
                message="Some notes round to zero duration at 480 PPQ; no notes were silently removed."))
        pitches = [note.pitch for note in notes]
        if min(pitches) < 40 or max(pitches) > 88:
            warnings.append(Diagnostic(code="pitch_out_of_range", blocks_generation=True,
                message="Notes lie outside standard six-string guitar frets 0–24 (MIDI 40–88)."))
        guitar = 24 <= program <= 31
        if not guitar:
            warnings.append(Diagnostic(code="non_guitar_program",
                message="Not labelled as a guitar; manual conversion is not an automatic guitar arrangement."))
        parts.append(MidiPart(id=part_id, track=track, channel=channel, program=program,
            name=names.get(track) or f"Track {track + 1}", note_count=len(notes),
            min_pitch=min(pitches), max_pitch=max(pitches), guitar_program=guitar, diagnostics=warnings))
    if not parts:
        diagnostics.append(Diagnostic(code="no_pitched_notes", blocks_generation=True,
                                      message="No pitched MIDI parts are available."))
    if rounded:
        diagnostics.append(Diagnostic(code="tick_rounding",
            message="Some source tick values were rounded to nearest 480-PPQ ticks; original ticks and seconds are retained."))
    return InspectedMidi(MidiInspection(source_midi_sha256=sha256(data).hexdigest(), source_ppq=ppq,
        tempo_map=tempo_map, meter_map=meter_map, parts=parts, ignored_drum_attacks=drums,
        rounded_tick_values=len(rounded), diagnostics=diagnostics), notes_by_part)
