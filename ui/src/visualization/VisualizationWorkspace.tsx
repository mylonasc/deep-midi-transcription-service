import { useEffect, useMemo, useState } from "react";
import { api, type Job } from "../lib/api";
import { parseMidi } from "./midi";
import { buildAlphaTex, type NotationMode } from "./alphaTex";
import { barStartSeconds, formatPlaybackTime, pageEndSeconds, pageStartSeconds, timeToPage } from "./playback";
import { useMidiPlayback } from "./useMidiPlayback";
import type { MidiSong } from "./types";
import AlphaTabView from "./AlphaTabView";
import PianoRoll from "./PianoRoll";

function PlaybackTransport({ current, duration, playing, error, onToggle, onRestart, onSeek }: {
  current: number; duration: number; playing: boolean; error: string | null;
  onToggle: () => void; onRestart: () => void; onSeek: (time: number) => void;
}) {
  return <div className="transport" aria-label="Synchronized preview playback">
    <div className="transport-controls">
      <button onClick={onToggle} disabled={!(duration > 0)}>{playing ? "Pause" : "Play"}</button>
      <button onClick={onRestart} disabled={!(duration > 0)}>Restart</button>
      <span role="status">{formatPlaybackTime(current)} / {formatPlaybackTime(duration)}</span>
    </div>
    <input aria-label="Visualization playback position" type="range" min={0} max={Math.max(0, duration)}
      step={0.05} value={Math.min(current, Math.max(0, duration))}
      onChange={event => onSeek(Number(event.target.value))} style={{ width: "100%" }} />
    {error && <p role="alert" className="notice">{error}</p>}
    <p className="form-hint">One preview engine drives both views. Play from either tab; the piano-roll playhead and notation page follow the same position.</p>
  </div>;
}

function SongViews({ song }: { song: MidiSong }) {
  const [view, setView] = useState("notation");
  const [trackId, setTrackId] = useState(song.tracks.find(track => !track.percussion)?.id ?? "");
  const [mode, setMode] = useState<NotationMode | "auto">("auto");
  const [page, setPage] = useState(0);
  const [follow, setFollow] = useState(true);
  const playback = useMidiPlayback(song);
  const playbackCurrent = playback.current;
  const playbackPlaying = playback.playing;
  const track = song.tracks.find(item => item.id === trackId);
  const resolvedMode: NotationMode = mode === "auto"
    ? track && track.program >= 24 && track.program < 32 ? "guitar"
      : track && track.program >= 32 && track.program < 40 ? "bass" : "score"
    : mode;
  const notation = useMemo(() => track && !track.percussion
    ? buildAlphaTex(song, track, resolvedMode, page) : null, [song, track, resolvedMode, page]);

  const pageStart = pageStartSeconds(song, page);
  const pageEnd = pageEndSeconds(song, page);

  useEffect(() => {
    if (!follow || !playbackPlaying) return;
    const next = timeToPage(song, playbackCurrent);
    if (next !== page) setPage(next);
  }, [follow, playbackPlaying, playbackCurrent, song, page]);

  const goToPage = (next: number) => {
    const pages = notation?.pages ?? 1;
    const clamped = Math.min(Math.max(0, next), pages - 1);
    setPage(clamped);
    playback.seek(pageStartSeconds(song, clamped));
  };

  const cursorFraction = playback.current < pageStart || playback.current > pageEnd || pageEnd <= pageStart
    ? null : (playback.current - pageStart) / (pageEnd - pageStart);

  return <>
    <PlaybackTransport current={playback.current} duration={playback.duration} playing={playback.playing}
      error={playback.error} onToggle={playback.toggle} onRestart={playback.restart} onSeek={playback.seek} />
    <label className="checkbox-label"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />Follow playback in notation and piano roll</label>
    <div className="view-switch" role="group" aria-label="Visualization view">
      <button aria-pressed={view === "notation"} onClick={() => setView("notation")}>Instrument notation</button>
      <button aria-pressed={view === "roll"} onClick={() => setView("roll")}>Piano roll</button>
    </div>
    {view === "roll" ? <PianoRoll song={song} currentTime={playback.current} playing={playback.playing} onSeek={playback.seek} /> : <>
      <div className="visual-controls">
        <div><label htmlFor="notation-track">Instrument track</label><select id="notation-track" value={trackId} onChange={event => { setTrackId(event.target.value); setPage(0); }}>
          {song.tracks.map(item => <option key={item.id} value={item.id}>{item.name} · {item.instrument}{item.percussion ? " (drums)" : ""}</option>)}
        </select></div>
        <div><label htmlFor="notation-mode">Notation format</label><select id="notation-mode" value={mode} onChange={event => setMode(event.target.value as NotationMode | "auto")}>
          <option value="auto">Auto by MIDI instrument</option><option value="score">Standard notation</option><option value="guitar">Guitar (E standard)</option><option value="bass">Bass (4-string standard)</option>
        </select></div>
      </div>
      <p className="prototype-note">Prototype: 4/4 measures, sixteenth-note quantization, four measures per page. Frets are a heuristic suggestion (24-fret limit), not the original fingering or guaranteed playable chords. Tempo/meter changes, expressive effects and percussion notation are not notated yet. The cursor shows proportional position within the page while the shared preview plays.</p>
      {track?.percussion ? <p className="empty-state">Percussion notation is not supported yet. Use Piano roll to see the original drum notes.</p>
        : notation ? <>
          {notation.omitted > 0 && <p className="notice" role="status">{notation.omitted} notes across this track could not be assigned a free string/fret and are omitted from tablature. They remain in the MIDI and piano roll.</p>}
          <div className="page-controls"><button disabled={page === 0} onClick={() => goToPage(page - 1)}>Previous measures</button>
            <span>Page {page + 1} of {notation.pages}</span><button disabled={page + 1 >= notation.pages} onClick={() => goToPage(page + 1)}>Next measures</button></div>
          <div className="measure-seek" role="group" aria-label="Seek to measure on this page">
            {[0, 1, 2, 3].map(bar => <button key={bar} onClick={() => playback.seek(barStartSeconds(song, page, bar))}>Measure {page * 4 + bar + 1}</button>)}
          </div>
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
  if (!song) return <p role="status">Loading MIDI visualization…</p>;
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
