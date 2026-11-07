import { Midi } from "@tonejs/midi";
import type { MidiSong } from "./types";

export function parseMidi(buffer: ArrayBuffer): MidiSong {
  const midi = new Midi(buffer);
  return {
    ppq: midi.header.ppq, tempo: midi.header.tempos[0]?.bpm ?? 120,
    duration: midi.duration, durationTicks: midi.durationTicks,
    tracks: midi.tracks.map((track, index) => ({
      id: String(index), name: track.name || `Track ${index + 1}`,
      instrument: track.instrument.name, program: track.instrument.number,
      percussion: track.instrument.percussion,
      notes: track.notes.map((note, n) => ({
        id: `${index}:${n}`, pitch: note.midi, startTick: note.ticks,
        durationTicks: note.durationTicks, time: note.time,
        duration: note.duration, velocity: note.velocity,
      })),
    })).filter(track => track.notes.length > 0),
  };
}
