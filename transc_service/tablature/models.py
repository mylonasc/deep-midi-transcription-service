"""Versioned boundaries for derived tablature; inference is added separately."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Diagnostic(StrictModel):
    code: str
    message: str
    blocks_generation: bool = False


class TempoChange(StrictModel):
    source_tick: int = Field(ge=0)
    tick: int = Field(ge=0)
    time_seconds: float = Field(ge=0)
    microseconds_per_beat: int = Field(gt=0)


class MeterChange(StrictModel):
    source_tick: int = Field(ge=0)
    tick: int = Field(ge=0)
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)


class MidiPart(StrictModel):
    id: str
    track: int = Field(ge=0)
    channel: int = Field(ge=0, le=15)  # MIDI wire convention, zero-based
    program: int = Field(ge=0, le=127)
    name: str
    note_count: int = Field(gt=0)
    min_pitch: int = Field(ge=0, le=127)
    max_pitch: int = Field(ge=0, le=127)
    guitar_program: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class MidiInspection(StrictModel):
    schema_version: Literal[1] = 1
    source_midi_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ppq: int = Field(gt=0)
    ppq: Literal[480] = 480
    tempo_map: list[TempoChange]
    meter_map: list[MeterChange]
    parts: list[MidiPart]
    ignored_drum_attacks: int = Field(ge=0)
    rounded_tick_values: int = Field(ge=0)
    rounding_policy: Literal["nearest_tick_ties_to_even"] = "nearest_tick_ties_to_even"
    equal_pitch_release_policy: Literal["FIFO_within_track_channel_pitch"] = "FIFO_within_track_channel_pitch"
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class SourceNote(StrictModel):
    id: str
    pitch: int = Field(ge=0, le=127)
    velocity: int = Field(ge=1, le=127)
    source_start_tick: int = Field(ge=0)
    source_end_tick: int = Field(gt=0)
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def positive_source_interval(self):
        if self.source_end_tick <= self.source_start_tick or self.end_seconds <= self.start_seconds:
            raise ValueError("source note must have positive duration")
        # Tick rounding can collapse a short interval. Inspection reports it;
        # the result validator below refuses it as a successful tablature.
        if self.end_tick < self.start_tick:
            raise ValueError("normalized note ends before its start")
        return self


class TablatureOptions(StrictModel):
    """Initial supported musical domain; model locations remain server-owned."""

    part_id: str = Field(pattern=r"^t\d+:c\d+:p\d+$")
    source_midi_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tuning: tuple[Literal[64], Literal[59], Literal[55], Literal[50], Literal[45], Literal[40]] = (64, 59, 55, 50, 45, 40)
    capo: Literal[0] = 0
    max_fret: Literal[24] = 24
    decoding: Literal["anchored"] = "anchored"


class Fingering(StrictModel):
    source_note_id: str
    string: int = Field(ge=1, le=6)  # 1 = highest-pitched open string
    fret: int = Field(ge=0, le=24)


class ModelProvenance(StrictModel):
    engine: Literal["open-fret"] = "open-fret"
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_id: str
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class NotationPage(StrictModel):
    index: int = Field(ge=0)
    start_tick: int = Field(ge=0)
    end_tick: int = Field(gt=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    measure_seconds: list[float]
    tex: str


class TablatureResult(StrictModel):
    """Canonical output: source timing is never reconstructed from TAB tokens."""

    schema_version: Literal[1] = 1
    options: TablatureOptions
    provenance: ModelProvenance
    source_ppq: int = Field(gt=0)
    ppq: Literal[480] = 480
    tempo_map: list[TempoChange] = Field(min_length=1)
    meter_map: list[MeterChange] = Field(min_length=1)
    ignored_part_ids: list[str] = Field(default_factory=list)
    ignored_drum_attacks: int = Field(default=0, ge=0)
    rounded_tick_values: int = Field(default=0, ge=0)
    rounding_policy: Literal["nearest_tick_ties_to_even"] = "nearest_tick_ties_to_even"
    equal_pitch_release_policy: Literal["FIFO_within_track_channel_pitch"] = "FIFO_within_track_channel_pitch"
    source_notes: list[SourceNote] = Field(min_length=1)
    assignments: list[Fingering] = Field(min_length=1)
    correction_counts: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    notation_pages: list[NotationPage] = Field(default_factory=list)

    @model_validator(mode="after")
    def preserve_notes_and_string_occupancy(self):
        notes = {note.id: note for note in self.source_notes}
        assigned = {assignment.source_note_id for assignment in self.assignments}
        if len(notes) != len(self.source_notes):
            raise ValueError("source note IDs must be unique")
        if len(self.assignments) != len(notes) or assigned != notes.keys():
            raise ValueError("exactly one assignment per source note is required")
        occupied_until = {}
        for assignment in sorted(self.assignments, key=lambda item: notes[item.source_note_id].start_tick):
            note = notes[assignment.source_note_id]
            if note.end_tick <= note.start_tick:
                raise ValueError("normalized note must have positive duration")
            pitch = self.options.tuning[assignment.string - 1] + self.options.capo + assignment.fret
            if pitch != note.pitch:
                raise ValueError("fingering must preserve source pitch")
            if occupied_until.get(assignment.string, 0) > note.start_tick:
                raise ValueError("overlapping notes cannot occupy the same string")
            occupied_until[assignment.string] = note.end_tick
        if any(count < 0 for count in self.correction_counts.values()):
            raise ValueError("correction counts cannot be negative")
        return self
