import type { MidiSong } from "./types";

export const MEASURES_PER_PAGE = 4;
export const STEPS_PER_MEASURE = 16;

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function midiNumberToName(pitch: number): string {
  const name = NAMES[((pitch % 12) + 12) % 12];
  return `${name}${Math.floor(pitch / 12) - 1}`;
}

export interface PlaybackNote {
  time: number;
  duration: number;
  name: string;
  velocity: number;
}

/** Flattened, time-sorted notes for the shared Tone.js preview engine. */
export function songToPlaybackNotes(song: MidiSong): PlaybackNote[] {
  return song.tracks
    .flatMap(track => track.notes.map(note => ({
      time: note.time,
      duration: note.duration,
      name: midiNumberToName(note.pitch),
      velocity: note.velocity,
    })))
    .sort((a, b) => a.time - b.time);
}

export function pageCount(song: MidiSong): number {
  if (song.ppq <= 0 || song.durationTicks <= 0) {
    return Math.max(1, Math.ceil(song.duration / 8));
  }
  return Math.max(1, Math.ceil(song.durationTicks / (song.ppq * MEASURES_PER_PAGE * 4)));
}

function secondsPerTick(song: MidiSong): number {
  if (song.durationTicks > 0 && song.duration > 0) return song.duration / song.durationTicks;
  if (song.ppq > 0 && song.tempo > 0) return 60 / (song.tempo * song.ppq);
  return 0;
}

export function pageStartSeconds(song: MidiSong, page: number): number {
  const ticksPerPage = song.ppq * MEASURES_PER_PAGE * 4;
  if (ticksPerPage <= 0) {
    const approx = 8 * page;
    return Math.min(song.duration, Math.max(0, approx));
  }
  return Math.min(song.duration, Math.max(0, page * ticksPerPage * secondsPerTick(song)));
}

export function pageEndSeconds(song: MidiSong, page: number): number {
  return Math.min(song.duration, pageStartSeconds(song, page + 1) || song.duration);
}

export function timeToPage(song: MidiSong, time: number): number {
  const pages = pageCount(song);
  if (!(song.duration > 0)) return 0;
  const clamped = Math.min(Math.max(0, time), Math.max(0, song.duration - 1e-6));
  return Math.min(pages - 1, Math.max(0, Math.floor((clamped / song.duration) * pages)));
}

export function barStartSeconds(song: MidiSong, page: number, bar: number): number {
  const start = pageStartSeconds(song, page);
  const end = pageEndSeconds(song, page);
  const clampedBar = Math.min(MEASURES_PER_PAGE - 1, Math.max(0, bar));
  return start + ((end - start) / MEASURES_PER_PAGE) * clampedBar;
}

export function formatPlaybackTime(s: number): string {
  const clamped = Math.max(0, s);
  return `${Math.floor(clamped / 60)}:${String(Math.floor(clamped % 60)).padStart(2, "0")}`;
}
