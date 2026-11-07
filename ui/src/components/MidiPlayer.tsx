import { useEffect, useRef, useState } from "react";
import * as Tone from "tone";
import { Midi } from "@tonejs/midi";

type Note = { time: number; duration: number; name: string; velocity: number };
const formatTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Short lookahead scheduling avoids allocating thousands of synth voices up front. */
export default function MidiPlayer({ midiUrl, title }: { midiUrl: string; title: string }) {
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const engine = useRef({ notes: [] as Note[], duration: 0, offset: 0, started: 0,
    playing: false, generation: 0, timer: 0, synth: null as Tone.PolySynth | null });

  function halt(update = true) {
    const e = engine.current;
    e.generation++;
    if (e.playing) e.offset = Math.min(e.duration, e.offset + Math.max(0, Tone.now() - e.started));
    e.playing = false;
    window.clearInterval(e.timer);
    e.synth?.dispose();
    e.synth = null;
    if (update) { setPlaying(false); setCurrent(e.offset); }
  }

  useEffect(() => {
    const abort = new AbortController();
    halt();
    engine.current.offset = 0;
    engine.current.notes = [];
    setCurrent(0);
    setDuration(0);
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const response = await fetch(midiUrl, { signal: abort.signal });
        if (!response.ok) throw new Error(`MIDI fetch: ${response.status}`);
        const midi = new Midi(await response.arrayBuffer());
        if (abort.signal.aborted) return;
        const notes = midi.tracks.flatMap(track => track.notes.map(n => ({
          time: n.time, duration: n.duration, name: n.name, velocity: n.velocity,
        }))).sort((a, b) => a.time - b.time);
        engine.current.notes = notes;
        engine.current.duration = midi.duration;
        setDuration(midi.duration);
      } catch (reason) {
        if (!abort.signal.aborted) setError(String(reason));
      } finally {
        if (!abort.signal.aborted) setLoading(false);
      }
    })();
    return () => { abort.abort(); halt(false); };
  }, [midiUrl]);

  async function play() {
    halt();
    const e = engine.current;
    const generation = e.generation;
    try {
      await Tone.start(); // Only ever called by a user gesture.
      if (generation !== e.generation) return;
      if (e.offset >= e.duration) e.offset = 0;
      const synth = new Tone.PolySynth(Tone.Synth).toDestination();
      synth.maxPolyphony = 128;
      synth.volume.value = -18;
      e.synth = synth;
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
          // Restore sustained notes when seeking into their duration.
          const when = Math.max(Tone.now(), e.started + Math.max(0, note.time - e.offset));
          synth.triggerAttackRelease(note.name, remaining, when, note.velocity);
        }
        setCurrent(Math.min(position, e.duration));
        if (position >= e.duration) { halt(); e.offset = e.duration; }
      };
      setPlaying(true);
      e.timer = window.setInterval(() => {
        try { pump(); } catch (reason) { halt(); setError(String(reason)); }
      }, 25);
      pump();
    } catch (reason) { halt(); setError(String(reason)); }
  }

  function seek(value: number) {
    const resume = engine.current.playing;
    halt();
    engine.current.offset = value;
    setCurrent(value);
    if (resume && value < engine.current.duration) void play();
  }

  if (loading) return <p>Loading MIDI…</p>;
  if (error) return <p role="alert">Player error: {error}</p>;
  return <div className="player">
    <strong>{title}</strong>
    <div className="player-controls">
      <button disabled={!duration} onClick={() => playing ? halt() : void play()}>{playing ? "Pause" : "Play"}</button>
      <button onClick={() => seek(0)}>Restart</button>
      <span>{formatTime(current)} / {formatTime(duration)}</span>
    </div>
    <input aria-label="MIDI playback position" type="range" min={0} max={duration} step={0.05}
      value={current} onChange={event => seek(Number(event.target.value))} style={{ width: "100%" }} />
    <small>Synthesized preview (not General MIDI instrument sounds). Drag to seek.</small>
  </div>;
}
