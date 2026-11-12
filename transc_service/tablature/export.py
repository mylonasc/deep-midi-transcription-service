"""Exact interval notation using Open-Fret's MIT duration decomposition.

Model inference and rendering are independent: this adapter never chooses frets.
"""
from bisect import bisect_right
from fractions import Fraction
import json

from .models import TablatureResult
from .durations import duration_pieces


def tick_seconds(result: TablatureResult, tick: int) -> float:
    source_tick = Fraction(tick * result.source_ppq, 480)
    changes = result.tempo_map
    i = bisect_right([event.source_tick for event in changes], source_tick) - 1
    event = changes[max(i, 0)]
    return event.time_seconds + float((source_tick - event.source_tick) * event.microseconds_per_beat
                                      / (result.source_ppq * 1_000_000))


def measure_boundaries(result: TablatureResult) -> list[int]:
    end = max(note.end_tick for note in result.source_notes)
    meters = {event.tick: event for event in result.meter_map}
    if 0 not in meters:
        raise ValueError("Meter map must start at tick zero")
    boundaries, tick = [0], 0
    meter = meters[0]
    while tick < end:
        meter = meters.get(tick, meter)
        duration = Fraction(480 * 4 * meter.numerator, meter.denominator)
        if duration.denominator != 1 or duration <= 0:
            raise ValueError("Meter has an unrepresentable measure duration")
        next_tick = tick + int(duration)
        if any(tick < event < min(next_tick, end) for event in meters):
            raise ValueError("Meter changes inside a measure are unsupported")
        tick = min(next_tick, end)
        boundaries.append(tick)
        if len(boundaries) > 10000:
            raise ValueError("Tablature exceeds the 10000-measure limit")
    return boundaries


def render_interval(result: TablatureResult, start: int, end: int, title="Generated guitar tablature") -> str:
    boundaries = measure_boundaries(result)
    notes = {note.id: note for note in result.source_notes}
    active_notes = [(notes[a.source_note_id], a) for a in result.assignments
                    if notes[a.source_note_id].start_tick < end and notes[a.source_note_id].end_tick > start]
    points = {start, end}
    for note, _ in active_notes:
        points.update((max(start, note.start_tick), min(end, note.end_tick)))
    points.update(t for t in boundaries if start < t < end)
    points.update(event.tick for event in result.meter_map if start < event.tick < end)
    meter = {event.tick: event for event in result.meter_map}
    initial_tempo = next(event for event in reversed(result.tempo_map) if event.tick <= start)
    initial_meter = next(event for event in reversed(result.meter_map) if event.tick <= start)
    # Fixed, server-authored title/track; no MIDI metadata becomes AlphaTex commands.
    header = [f"\\title {json.dumps(title)}", f"\\tempo {60_000_000 / initial_tempo.microseconds_per_beat:.12g}",
              '\\track "Guitar"', '\\instrument 24', '\\staff {score tabs}',
              '\\tuning (e4 b3 g3 d3 a2 e2)', '.',
              f"\\ts ({initial_meter.numerator} {initial_meter.denominator})"]
    body = []
    sorted_points = sorted(points)
    for left, right in zip(sorted_points, sorted_points[1:]):
        if left > start and left in boundaries:
            body.append("|")
        if left > start and left in meter:
            event = meter[left]
            body.append(f"\\ts ({event.numerator} {event.denominator})")
        if left == start or left in boundaries:
            effective_meter = next(event for event in reversed(result.meter_map) if event.tick <= left)
            bar_length = Fraction(480 * 4 * effective_meter.numerator, effective_meter.denominator)
            # AlphaTex beat tempo effects accept integer BPM only. Bar tempo
            # metadata accepts floating BPM plus a fractional bar position, so
            # original microsecond tempi need not be silently rounded.
            for change in result.tempo_map:
                if left <= change.tick < min(left + bar_length, end) and change.tick > start:
                    ratio = float(Fraction(change.tick - left, 1) / bar_length)
                    body.append(f'\\tempo ({60_000_000 / change.microseconds_per_beat:.15g} "" {ratio:.15g})')
        sounding = sorted([(note, a) for note, a in active_notes if note.start_tick <= left < note.end_tick],
                          key=lambda item: item[1].string)
        tick = left
        for piece in duration_pieces(right - left):
            tokens = [f"{'-' if note.start_tick < tick and tick > start else a.fret}.{a.string}"
                      for note, a in sounding]
            beat = "(" + " ".join(tokens) + ")" if len(tokens) > 1 else tokens[0] if tokens else "r"
            effects = piece.effects().strip("{}")
            body.append(f":{piece.denominator} {beat}" + ("{" + effects.strip() + "}" if effects else ""))
            tick += piece.ticks
    return "\n".join(header + [" ".join(body)]) + "\n"


def export_result(result: TablatureResult) -> dict[str, bytes]:
    boundaries = measure_boundaries(result)
    pages = []
    for index, offset in enumerate(range(0, len(boundaries) - 1, 4)):
        start, end = boundaries[offset], boundaries[min(offset + 4, len(boundaries) - 1)]
        pages.append({"index": index, "start_tick": start, "end_tick": end,
                      "start_seconds": tick_seconds(result, start), "end_seconds": tick_seconds(result, end),
                      "measure_seconds": [tick_seconds(result, t) for t in boundaries[offset:offset + 4] if t < end],
                      "tex": render_interval(result, start, end)})
    document = result.model_dump(mode="json")
    document["notation_pages"] = pages
    # ASCII deliberately represents attacks only, not independently sustained durations.
    notes = {note.id: note for note in result.source_notes}
    onsets = sorted({note.start_tick for note in result.source_notes})
    columns = []
    for tick in onsets:
        cells = {a.string: str(a.fret) for a in result.assignments if notes[a.source_note_id].start_tick == tick}
        width = max(2, max(map(len, cells.values())) + 1)
        columns.append([cells.get(string, "-").ljust(width, "-") for string in range(1, 7)])
    ascii_text = "Onset preview only; spacing does not encode note durations.\n"
    ascii_text += "\n".join(f"{label}|" + "".join(column[i] for column in columns)
                             for i, label in enumerate(["e", "B", "G", "D", "A", "E"])) + "\n"
    return {"tablature.json": json.dumps(document).encode(),
            "score.tex": render_interval(result, 0, boundaries[-1]).encode(),
            "preview.txt": ascii_text.encode()}
