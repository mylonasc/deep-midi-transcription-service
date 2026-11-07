import { lazy, Suspense, useState } from "react";
import MidiPlayer from "./components/MidiPlayer";
import Notice from "./components/ui/Notice";
import SiteHeader from "./app/SiteHeader";
import WorkspaceTabs from "./app/WorkspaceTabs";
import QueueForm from "./features/jobs/QueueForm";
import JobsSection from "./features/jobs/JobsSection";
import ArtifactLibrary from "./features/artifacts/ArtifactLibrary";
import { api } from "./lib/api";
import { useJobPolling } from "./lib/useJobPolling";

const VisualizationWorkspace = lazy(() => import("./visualization/VisualizationWorkspace"));

export default function App() {
  const { jobs, loaded, connectionError, refresh } = useJobPolling();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("all");
  const [tab, setTab] = useState("jobs");

  async function action(id: string, operation: () => Promise<unknown>) {
    setBusy(current => new Set(current).add(id));
    setError(null);
    try { await operation(); await refresh(); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(current => { const next = new Set(current); next.delete(id); return next; }); }
  }

  const playerJob = jobs.find(job => job.id === selected) ?? jobs.find(job => job.midi_key);

  function selectTab(next: string) {
    setTab(next);
    requestAnimationFrame(() => document
      .getElementById(next === "jobs" ? "panel-jobs" : "panel-visualization")?.focus({ preventScroll: true }));
  }

  function preview(id: string) {
    setSelected(id);
    requestAnimationFrame(() => document.getElementById("player")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  return <>
    <a className="skip-link" href="#main">Skip to content</a>
    <SiteHeader loaded={loaded} connectionError={connectionError} />

    <main id="main" className="app-shell">
      <WorkspaceTabs tab={tab} onChange={selectTab} />
      {tab === "jobs" ? <div id="panel-jobs" role="tabpanel" aria-labelledby="tab-jobs" tabIndex={-1}>
      {connectionError && <Notice>Could not refresh jobs. Retrying automatically. <span>{connectionError}</span></Notice>}
      {error && <Notice onDismiss={() => setError(null)}>{error}</Notice>}

      <div className="workspace">
        <QueueForm onQueued={job => { setSelected(job.id); setFilter("all"); void refresh(); }} />
        <JobsSection jobs={jobs} loaded={loaded} filter={filter} onFilter={setFilter}
          busy={busy} onAction={(id, operation) => void action(id, operation)} onPlay={preview} />
      </div>

      {playerJob?.midi_key && <section id="player" className="player-section" aria-label="MIDI preview"><p className="eyebrow">LISTEN BACK</p><h2>MIDI preview</h2><MidiPlayer midiUrl={api.midiUrl(playerJob.id)} title={playerJob.title || playerJob.id} /></section>}
      <ArtifactLibrary jobs={jobs} onPlay={preview} />
      </div> : <div id="panel-visualization" role="tabpanel" aria-labelledby="tab-visualization" tabIndex={-1}><Suspense fallback={<p role="status">Loading visualization tools…</p>}><VisualizationWorkspace jobs={jobs} /></Suspense></div>}
      <footer>Deep MIDI · Powered by muscriptor</footer>
    </main>
  </>;
}
