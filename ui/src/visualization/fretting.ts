import type { SongNote } from "./types";

export interface FrettedNote extends SongNote { string: number; fret: number }
export interface FrettingResult { notes: FrettedNote[]; omitted: number }
export interface FretResolver {
  resolve(notes: SongNote[], tuning: readonly number[], maxFret: number): FrettingResult;
}
// String 1 is highest-pitched, matching alphaTab (but not dependent on it).
export const GUITAR_TUNING = [64, 59, 55, 50, 45, 40] as const;
export const BASS_TUNING = [43, 38, 33, 28] as const;

/** Fast heuristic, not an optimal fingering solver. Never puts overlapping notes
 * on the same string. Replace via FretResolver without touching score rendering. */
export const greedyFretResolver: FretResolver = {
  resolve(notes, tuning, maxFret) {
    const result: FrettedNote[] = [];
    const busyUntil = tuning.map(() => 0);
    let handPosition = 3;
    let omitted = 0;
    const ordered = [...notes].sort((a, b) => a.startTick - b.startTick || b.pitch - a.pitch);
    for (const note of ordered) {
      const candidates = tuning.flatMap((open, index) => {
        const fret = note.pitch - open;
        return fret >= 0 && fret <= maxFret && busyUntil[index] <= note.startTick
          ? [{ string: index + 1, fret, cost: Math.abs(fret - handPosition) + fret * 0.08 }] : [];
      }).sort((a, b) => a.cost - b.cost || a.string - b.string);
      const best = candidates[0];
      if (!best) { omitted++; continue; }
      busyUntil[best.string - 1] = note.startTick + note.durationTicks;
      if (best.fret > 0) handPosition = best.fret;
      result.push({ ...note, string: best.string, fret: best.fret });
    }
    return { notes: result, omitted };
  },
};
