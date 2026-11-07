import { useCallback, useEffect, useRef, useState } from "react";
import * as Tone from "tone";
import type { MidiSong } from "./types";
import { songToPlaybackNotes } from "./playback";

/**
 * Shared Tone.js preview engine for the visualization workspace.
 * Plays the already-parsed song so piano roll and notation stay in sync.
 * Short-lookahead scheduling avoids allocating thousands of voices up front.
 */
export function useMidiPlayback(song: MidiSong) {
  const [current, setCurrent] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const engine = useRef({
    notes: songToPlaybackNotes(song),
    duration: song.duration,
    offset: 0,
    started: 0,
    playing: false,
    generation: 0,
    timer: 0,
  });
  const synth = useRef<Tone.PolySynth | null>(null);

  const halt = useCallback((update = true) => {
    const e = engine.current;
    e.generation++;
    if (e.playing) e.offset = Math.min(e.duration, e.offset + Math.max(0, Tone.now() - e.started));
    e.playing = false;
    window.clearInterval(e.timer);
    synth.current?.dispose();
    synth.current = null;
    if (update) {
      setPlaying(false);
      setCurrent(e.offset);
    }
  }, []);

  useEffect(() => {
    halt(false);
    engine.current.notes = songToPlaybackNotes(song);
    engine.current.duration = song.duration;
    engine.current.offset = 0;
    setCurrent(0);
    setPlaying(false);
    setError(null);
    return () => halt(false);
  }, [song, halt]);

  useEffect(() => () => halt(false), [halt]);

  const play = useCallback(async () => {
    halt();
    const e = engine.current;
    const generation = e.generation;
    try {
      await Tone.start(); // Only ever called by a user gesture.
      if (generation !== e.generation) return;
      if (e.offset >= e.duration) e.offset = 0;
      const voice = new Tone.PolySynth(Tone.Synth).toDestination();
      voice.maxPolyphony = 128;
      voice.volume.value = -18;
      synth.current = voice;
      e.started = Tone.now() + 0.05;
      e.playing = true;
      let index = 0;
      while (index < e.notes.length && e.notes[index].time + e.notes[index].duration <= e.offset) index++;
      const pump = () => {
        const position = Math.max(e.offset, e.offset + Tone.now() - e.started);
        while (index < e.notes.length && e.notes[index].time <= position + 0.15) {
          const note = e.notes[index++];
          const remaining = note.time + note.duration - Math.max(e.offset, note.time);
          if (remaining <= 0) continue;
          const when = Math.max(Tone.now(), e.started + Math.max(0, note.time - e.offset));
          voice.triggerAttackRelease(note.name, remaining, when, note.velocity);
        }
        setCurrent(Math.min(position, e.duration));
        if (position >= e.duration) {
          halt();
          engine.current.offset = engine.current.duration;
        }
      };
      setPlaying(true);
      e.timer = window.setInterval(() => {
        try {
          pump();
        } catch (reason) {
          halt();
          setError(String(reason));
        }
      }, 25);
      pump();
    } catch (reason) {
      halt();
      setError(String(reason));
    }
  }, [halt]);

  const seek = useCallback((value: number) => {
    const resume = engine.current.playing;
    const clamped = Math.min(Math.max(0, value), engine.current.duration);
    halt();
    engine.current.offset = clamped;
    setCurrent(clamped);
    if (resume && clamped < engine.current.duration) void play();
  }, [halt, play]);

  const pause = useCallback(() => halt(), [halt]);

  return {
    current,
    duration: song.duration,
    playing,
    error,
    play: () => void play(),
    pause,
    toggle: () => { if (engine.current.playing) pause(); else void play(); },
    seek,
    restart: () => seek(0),
  };
}
