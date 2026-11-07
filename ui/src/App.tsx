import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import MidiPlayer from "./components/MidiPlayer";
import JobCard, { ACTIVE } from "./components/JobCard";
import ArtifactList from "./components/ArtifactList";
import ModelHealth from "./components/ModelHealth";
import { api, type Job } from "./lib/api";

const VisualizationWorkspace = lazy(() => import("./visualization/VisualizationWorkspace"));

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("medium");
  const [maxDur, setMaxDur] = useState(600);
  const [allowMix, setAllowMix] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("all");
  const [tab, setTab] = useState("jobs");

  const refresh = useCallback(async () => {
    try { setJobs(await api.list()); setConnectionError(null); }
    catch (reason) { setConnectionError(String(reason)); }
    finally { setLoaded(true); }
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!disposed) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [refresh]);

  async function submit() {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const job: Job = await api.create(url.trim(), model, maxDur, allowMix);
      setUrl(""); setSelected(job.id); setFilter("all");
      await refresh();
    } catch (reason) { setError(String(reason)); }
    finally { setSubmitting(false); }
  }

  async function action(id: string, operation: () => Promise<unknown>) {
    setBusy(current => new Set(current).add(id));
    setError(null);
    try { await operation(); await refresh(); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(current => { const next = new Set(current); next.delete(id); return next; }); }
  }

  const active = jobs.filter(job => ACTIVE.has(job.status)).length;
  const completed = jobs.filter(job => job.status === "done").length;
  const failed = jobs.filter(job => job.status === "failed").length;
  const visible = jobs.filter(job => filter === "all" || (filter === "active" ? ACTIVE.has(job.status) : job.status === filter));
  const playerJob = jobs.find(job => job.id === selected) ?? jobs.find(job => job.midi_key);

  function preview(id: string) {
    setSelected(id);
    requestAnimationFrame(() => document.getElementById("player")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  return <>
    <a className="skip-link" href="#main">Skip to content</a>
    <header className="site-header"><div className="header-inner">
      <div className="brand-mark" aria-hidden="true">♫</div>
      <div><p className="eyebrow">DEEP MIDI</p><h1>Your music, transcribed.</h1><p className="header-copy">From YouTube to MIDI, in one queue.</p></div>
      <span className={`connection ${connectionError ? "offline" : ""}`}><span aria-hidden="true">●</span> {connectionError ? "Reconnecting" : loaded ? "Connected" : "Connecting"}</span>
    </div></header>

    <main id="main" className="app-shell">
      <div className="app-tabs" role="tablist" aria-label="Workspace" onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          const next = event.key === "Home" ? "jobs" : event.key === "End" ? "visualization" : tab === "jobs" ? "visualization" : "jobs";
          setTab(next); document.getElementById(`tab-${next}`)?.focus();
        }
      }}>
        <button id="tab-jobs" role="tab" aria-selected={tab === "jobs"} aria-controls="panel-jobs" tabIndex={tab === "jobs" ? 0 : -1} onClick={() => setTab("jobs")}>Jobs & artifacts</button>
        <button id="tab-visualization" role="tab" aria-selected={tab === "visualization"} aria-controls="panel-visualization" tabIndex={tab === "visualization" ? 0 : -1} onClick={() => setTab("visualization")}>Visualization</button>
      </div>
      {tab === "jobs" ? <div id="panel-jobs" role="tabpanel" aria-labelledby="tab-jobs">
      <div className="summary-grid" aria-label="Job summary">
        <div><strong>{active}</strong><span>In progress</span></div>
        <div><strong>{completed}</strong><span>Complete</span></div>
        <div><strong>{failed}</strong><span>Need attention</span></div>
      </div>
      {connectionError && <div className="notice" role="alert">Could not refresh jobs. Retrying automatically. <span>{connectionError}</span></div>}
      {error && <div className="notice" role="alert"><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}

      <div className="workspace">
        <section className="panel queue-panel" aria-labelledby="queue-heading">
          <p className="eyebrow">NEW TRANSCRIPTION</p><h2 id="queue-heading">Queue a song</h2>
          <p className="section-copy">Paste a video link. We’ll download the audio and create your MIDI.</p>
          <form onSubmit={event => { event.preventDefault(); void submit(); }}>
            <label htmlFor="youtube-url">YouTube URL</label>
            <input id="youtube-url" type="url" required placeholder="https://youtube.com/watch?v=…" value={url} onChange={event => setUrl(event.target.value)} />
            <div className="form-grid">
              <div><label htmlFor="model">Model</label><select id="model" value={model} onChange={event => setModel(event.target.value)}>
                <option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option>
              </select></div>
              <div><label htmlFor="duration">Max duration (seconds)</label><input id="duration" type="number" min={1} required value={maxDur} onChange={event => setMaxDur(Number(event.target.value))} /></div>
            </div>
            <label className="checkbox-label"><input type="checkbox" checked={allowMix} onChange={event => setAllowMix(event.target.checked)} />Allow mixes</label>
            <ModelHealth selectedModel={model} />
            <button className="button-primary submit-button" type="submit" disabled={submitting || !url.trim() || !Number.isFinite(maxDur) || maxDur < 1}>{submitting ? "Queuing…" : "Queue transcription"}</button>
            <p className="form-hint">One video per job. Larger models need more time and memory.</p>
          </form>
        </section>

        <section className="jobs-section" aria-labelledby="jobs-heading">
          <div className="section-heading"><div><p className="eyebrow">WORKSPACE</p><h2 id="jobs-heading">Jobs <span className="count">{jobs.length}</span></h2></div>
            <div className="filter-control"><label htmlFor="status-filter">Show</label><select id="status-filter" value={filter} onChange={event => setFilter(event.target.value)}>
              <option value="all">All jobs</option><option value="active">In progress</option><option value="done">Complete</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option>
            </select></div>
          </div>
          <p className="live-caption">Status refreshes every 2 seconds</p>
          {!loaded ? <div className="empty-state" role="status">Loading jobs…</div>
            : visible.length === 0 ? <div className="empty-state"><h3>{jobs.length ? "No matching jobs" : "Your queue is ready"}</h3><p>{jobs.length ? "Choose another status to see more jobs." : "Add a YouTube link to create your first transcription."}</p></div>
            : <div className="job-list">{visible.map(job => <JobCard key={job.id} job={job} busy={busy.has(job.id)} onAction={(id, operation) => void action(id, operation)} onPlay={preview} />)}</div>}
        </section>
      </div>

      {playerJob?.midi_key && <section id="player" className="player-section" aria-label="MIDI preview"><p className="eyebrow">LISTEN BACK</p><h2>MIDI preview</h2><MidiPlayer midiUrl={api.midiUrl(playerJob.id)} title={playerJob.title || playerJob.id} /></section>}
      <ArtifactList jobs={jobs} onPlay={preview} />
      </div> : <div id="panel-visualization" role="tabpanel" aria-labelledby="tab-visualization"><Suspense fallback={<p role="status">Loading visualization tools…</p>}><VisualizationWorkspace jobs={jobs} /></Suspense></div>}
      <footer>Deep MIDI · Powered by muscriptor</footer>
    </main>
  </>;
}
