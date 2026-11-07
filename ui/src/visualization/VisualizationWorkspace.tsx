import { useEffect, useState } from "react";
import { api, type Job } from "../lib/api";
import { parseMidi } from "./midi";
import type { MidiSong } from "./types";
import { useVisualizationState } from "./useVisualizationState";
import AlphaTabView from "./AlphaTabView";
import PageNavigator from "./PageNavigator";
import PianoRoll from "./PianoRoll";
import Transport from "./Transport";

function SongViews({ song }: { song: MidiSong }) {
  const state = useVisualizationState(song);
  const { view, setView, trackId, selectTrack, mode, setMode, page } = state;
  const { track, notation, cursorFraction, playback, follow, setFollow, goToPage, seekMeasure } = state;

  return <>
    <Transport current={playback.current} duration={playback.duration} playing={playback.playing}
      error={playback.error} onToggle={playback.toggle} onRestart={playback.restart} onSeek={playback.seek} />
    <label className="checkbox-label"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />Follow playback in notation and piano roll</label>
    <div className="view-switch" role="group" aria-label="Visualization view">
      <button aria-pressed={view === "notation"} onClick={() => setView("notation")}>Instrument notation</button>
      <button aria-pressed={view === "roll"} onClick={() => setView("roll")}>Piano roll</button>
    </div>
    {view === "roll" ? <PianoRoll song={song} currentTime={playback.current} playing={playback.playing} onSeek={playback.seek} /> : <>
      <div className="visual-controls">
        <div><label htmlFor="notation-track">Instrument track</label><select id="notation-track" value={trackId} onChange={event => selectTrack(event.target.value)}>
          {song.tracks.map(item => <option key={item.id} value={item.id}>{item.name} · {item.instrument}{item.percussion ? " (drums)" : ""}</option>)}
        </select></div>
        <div><label htmlFor="notation-mode">Notation format</label><select id="notation-mode" value={mode} onChange={event => setMode(event.target.value as typeof mode)}>
          <option value="auto">Auto by MIDI instrument</option><option value="score">Standard notation</option><option value="guitar">Guitar (E standard)</option><option value="bass">Bass (4-string standard)</option>
        </select></div>
      </div>
      <p className="prototype-note">Prototype: 4/4 measures, sixteenth-note quantization, four measures per page. Frets are a heuristic suggestion (24-fret limit), not the original fingering or guaranteed playable chords. Tempo/meter changes, expressive effects and percussion notation are not notated yet. The cursor shows proportional position within the page while the shared preview plays.</p>
      {track?.percussion ? <p className="empty-state">Percussion notation is not supported yet. Use Piano roll to see the original drum notes.</p>
        : notation ? <>
          {notation.omitted > 0 && <p className="notice" role="status">{notation.omitted} notes across this track could not be assigned a free string/fret and are omitted from tablature. They remain in the MIDI and piano roll.</p>}
          {notation && <PageNavigator page={page} pages={notation.pages} onPage={goToPage} onMeasure={seekMeasure} />}
          <AlphaTabView tex={notation.tex} cursorFraction={cursorFraction} />
        </> : <p className="empty-state">No pitched tracks available. Try the piano roll.</p>}
    </>}
  </>;
}

function MidiVisualization({ jobId }: { jobId: string }) {
  const [song, setSong] = useState<MidiSong | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    void (async () => {
      try {
        const response = await fetch(api.midiUrl(jobId), { signal: abort.signal });
        if (!response.ok) throw new Error(`Could not load MIDI (${response.status})`);
        const parsed = parseMidi(await response.arrayBuffer());
        if (!abort.signal.aborted) setSong(parsed);
      } catch (reason) { if (!abort.signal.aborted) setError(String(reason)); }
    })();
    return () => abort.abort();
  }, [jobId]);
  if (error) return <p role="alert" className="notice">{error}</p>;
  if (!song) return <div aria-busy="true"><div className="skeleton skeleton-line" aria-hidden="true" /><div className="skeleton skeleton-block" aria-hidden="true" /><p role="status">Loading MIDI visualization…</p></div>;
  if (!song.tracks.length) return <p className="empty-state">This MIDI contains no notes to visualize.</p>;
  return <SongViews key={song.duration} song={song} />;
}

export default function VisualizationWorkspace({ jobs }: { jobs: Job[] }) {
  const available = jobs.filter(job => job.midi_key);
  const [selected, setSelected] = useState("");
  const selectedJob = available.find(job => job.id === selected) ?? available[0];
  return <section className="panel visualization-workspace" aria-labelledby="visual-heading">
    <p className="eyebrow">EXPLORE YOUR TRANSCRIPTION</p><h2 id="visual-heading">MIDI visualization</h2>
    <p className="section-copy">Read individual instrument parts with alphaTab, or explore the original notes in a separate piano roll. Both stay synchronized with one preview playback engine.</p>
    {!selectedJob ? <p className="empty-state">Complete a transcription first. Your MIDI songs will appear here.</p> : <>
      <label htmlFor="visual-song">MIDI song</label><select id="visual-song" value={selectedJob.id} onChange={event => setSelected(event.target.value)}>
        {available.map(job => <option key={job.id} value={job.id}>{job.title || job.id}</option>)}
      </select>
      <MidiVisualization key={selectedJob.id} jobId={selectedJob.id} />
    </>}
  </section>;
}
