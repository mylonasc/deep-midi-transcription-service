import type { MidiSong, SongNote, SongTrack } from "./types";
import { BASS_TUNING, GUITAR_TUNING, greedyFretResolver, type FretResolver, type FrettedNote } from "./fretting";

export type NotationMode = "score" | "guitar" | "bass";
const names = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"];
const pitchName = (pitch: number) => `${names[pitch % 12]}${Math.floor(pitch / 12) - 1}`;
// Avoid interpreting user-supplied titles as alphaTex commands.
const quote = (value: string) => `"${value.replace(/["\\\r\n]/g, " ").slice(0, 120)}"`;

/** Prototype adapter: four 4/4 measures per page, quantized to sixteenths.
 * Original notes/timings remain unchanged for the independent piano roll. */
export function buildAlphaTex(song: MidiSong, track: SongTrack, mode: NotationMode,
  page: number, resolver: FretResolver = greedyFretResolver) {
  const step = song.ppq / 4;
  const quantized = track.notes.map(note => ({ ...note,
    startTick: Math.round(note.startTick / step),
    durationTicks: Math.max(1, Math.round((note.startTick + note.durationTicks) / step) - Math.round(note.startTick / step)),
  }));
  const tuning = mode === "bass" ? BASS_TUNING : GUITAR_TUNING;
  const fretted = mode === "score" ? null : resolver.resolve(quantized, tuning, 24);
  const notes: (SongNote | FrettedNote)[] = fretted?.notes ?? quantized;
  const start = page * 64;
  const bars: string[] = [];
  let previous = new Set<string>();
  for (let bar = 0; bar < 4; bar++) {
    const beats: string[] = [];
    for (let slot = start + bar * 16; slot < start + (bar + 1) * 16; slot++) {
      const sounding = notes.filter(note => note.startTick <= slot && note.startTick + note.durationTicks > slot);
      const tokens = sounding.map(note => {
        if (mode !== "score" && "string" in note) return `${previous.has(note.id) ? "-" : note.fret}.${note.string}`;
        return `${pitchName(note.pitch)}${previous.has(note.id) ? "{t}" : ""}`;
      });
      previous = new Set(sounding.map(note => note.id));
      beats.push(tokens.length > 1 ? `(${tokens.join(" ")})` : tokens.length ? tokens[0] : "r");
    }
    bars.push(beats.join(" "));
  }
  const staff = mode === "score" ? "\\staff {score}\n\\tuning piano" : `\\staff {score tabs}\n\\tuning (${tuning.map(pitchName).join(" ")})`;
  return {
    tex: `\\title ${quote(track.name)}\n\\subtitle ${quote(`Measures ${page * 4 + 1}–${page * 4 + 4} · prototype`)}\n\\tempo ${Math.round(song.tempo)}\n\\track ${quote(track.instrument)}\n${staff}\n.\n\\ts (4 4)\n:16 ${bars.join(" | ")}`,
    omitted: fretted?.omitted ?? 0,
    pages: Math.max(1, Math.ceil(song.durationTicks / (song.ppq * 16))),
  };
}
