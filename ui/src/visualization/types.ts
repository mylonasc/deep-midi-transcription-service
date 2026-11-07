/** Renderer-neutral MIDI data. No alphaTab or fretboard dependencies. */
export interface SongNote {
  id: string;
  pitch: number;
  startTick: number;
  durationTicks: number;
  time: number;
  duration: number;
  velocity: number;
}
export interface SongTrack {
  id: string;
  name: string;
  instrument: string;
  program: number;
  percussion: boolean;
  notes: SongNote[];
}
export interface MidiSong {
  ppq: number;
  tempo: number;
  duration: number;
  durationTicks: number;
  tracks: SongTrack[];
}
