import { useEffect, useRef, useState } from "react";
import { api, type MidiInspection, type TabTask, type TabHealth, type TabResult } from "../lib/api";
import AlphaTabView from "./AlphaTabView";

/** Stored backend fingerings only. No MIDI track-index assumptions or second player. */
export default function TablaturePanel({ jobId, sourceHash, current, playing, follow, onSeek }: {
  jobId: string; sourceHash: string | null; current: number; playing: boolean; follow: boolean; onSeek: (time: number) => void;
}) {
  const [inspection, setInspection] = useState<MidiInspection | null>(null);
  const [health, setHealth] = useState<TabHealth | null>(null);
  const [tasks, setTasks] = useState<TabTask[]>([]);
  const [partId, setPartId] = useState("");
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<TabResult | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lifecycle = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController(); lifecycle.current = abort;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const [parts, nextTasks, nextHealth] = await Promise.all([
          api.midiParts(jobId, abort.signal), api.tabs(jobId, abort.signal), api.tabHealth(abort.signal),
        ]);
        if (abort.signal.aborted) return;
        setInspection(parts); setTasks(nextTasks); setHealth(nextHealth); setError(null);
      } catch (reason) {
        if (!abort.signal.aborted) { setHealth(null); setError(String(reason)); }
      } finally { if (!abort.signal.aborted) timer = setTimeout(() => void refresh(), 2000); }
    };
    void refresh();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [jobId]);
  const selectedTask = tasks.find(task => task.id === selected);
  const selectedPart = inspection?.parts.find(part => part.id === partId);
  const diagnostics = [...inspection?.diagnostics ?? [], ...selectedPart?.diagnostics ?? []];
  const staleSource = Boolean(sourceHash && inspection && sourceHash !== inspection.source_midi_sha256);
  const readyTaskId = selectedTask?.status === "done" ? selectedTask.id : "";
  useEffect(() => {
    const abort = new AbortController();
    setResult(null); setPageIndex(0);
    if (readyTaskId) void api.tabResult(readyTaskId, abort.signal).then(next => {
      if (abort.signal.aborted) return;
      if (next.options.source_midi_sha256 !== inspection?.source_midi_sha256 || (sourceHash && sourceHash !== next.options.source_midi_sha256)) {
        setError("Source MIDI has changed. Reload the song before generating or viewing tablature."); return;
      }
      setResult(next);
    }).catch(reason => { if (!abort.signal.aborted) setError(String(reason)); });
    return () => abort.abort();
  }, [readyTaskId, inspection?.source_midi_sha256, sourceHash]);
  useEffect(() => {
    if (!follow || !playing || !result) return;
    const next = result.notation_pages.findIndex(page => current >= page.start_seconds && current < page.end_seconds);
    if (next >= 0) setPageIndex(next);
  }, [current, playing, follow, result]);

  async function action(operation: (signal: AbortSignal) => Promise<unknown>) {
    const signal = lifecycle.current?.signal;
    if (!signal || busy) return;
    setBusy(true); setError(null);
    try {
      await operation(signal);
      const next = await api.tabs(jobId, signal);
      if (!signal.aborted) setTasks(next);
    } catch (reason) { if (!signal.aborted) setError(String(reason)); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  const page = result?.notation_pages[pageIndex];
  const cursor = page && current >= page.start_seconds && current <= page.end_seconds
    ? (current - page.start_seconds) / (page.end_seconds - page.start_seconds) : null;
  function goToPage(index: number) {
    const next = result?.notation_pages[index];
    if (next) { setPageIndex(index); onSeek(next.start_seconds); }
  }
  return <section aria-labelledby="generated-tab-heading">
    <h3 id="generated-tab-heading">Open-Fret generated tablature</h3>
    <p className="prototype-note">Persisted model-assisted fingerings, not the greedy prototype preview. One selected part, standard six-string tuning, capo 0, frets 0–24. Playback still uses the original MIDI and the shared transport. Fingerings are not guaranteed ergonomic.</p>
    {error && <p className="notice" role="alert">{error}</p>}
    {staleSource && <p className="notice" role="alert">Source MIDI changed since playback loaded. Reload this song before using generated tablature.</p>}
    <p role="status">Tablature model: {health ? `${health.status.replaceAll("_", " ")} · ${health.message}` : "Availability unknown"}</p>
    {inspection ? <>
      <label htmlFor="tab-part">Guitar tablature source part</label>
      <select id="tab-part" value={partId} onChange={event => setPartId(event.target.value)}>
        <option value="">Choose a MIDI part</option>
        {inspection.parts.map(part => <option key={part.id} value={part.id}>{part.name} · program {part.program} · channel {part.channel + 1} · {part.note_count} notes · pitches {part.min_pitch}–{part.max_pitch}</option>)}
      </select>
      {inspection.ignored_drum_attacks > 0 && <p className="form-hint">{inspection.ignored_drum_attacks} drum attacks excluded. Unselected instruments are not arranged for guitar.</p>}
      {diagnostics.map(item => <p className={item.blocks_generation ? "notice" : "form-hint"} key={item.code}>{item.message}</p>)}
      <button disabled={busy || staleSource || !selectedPart || diagnostics.some(item => item.blocks_generation) || !health || health.status === "unavailable"}
        onClick={() => void action(async signal => {
          const task = await api.createTab(jobId, partId, inspection.source_midi_sha256, signal);
          if (!signal.aborted) setSelected(task.id);
        })}>Generate tablature</button>
    </> : <p role="status">Loading MIDI parts…</p>}
    {tasks.length > 0 && <>
      <label htmlFor="tab-result">Tablature task / result</label>
      <select id="tab-result" value={selected} onChange={event => setSelected(event.target.value)}>
        <option value="">Choose a task</option>
        {tasks.map(task => <option key={task.id} value={task.id}>{task.options.part_id} · {task.status} · attempt {task.attempt + 1} · {task.id.slice(0, 8)}</option>)}
      </select>
    </>}
    {selectedTask && <>
      <p role="status">{selectedTask.status} · {selectedTask.phase}{selectedTask.total > 0 ? ` · ${selectedTask.completed}/${selectedTask.total}` : ""}</p>
      {selectedTask.error && <pre className="tab-error" role="alert">{selectedTask.error}</pre>}
      <div className="artifact-actions">
        {["queued", "generating"].includes(selectedTask.status) && <button disabled={busy} onClick={() => void action(signal => api.cancelTab(selectedTask.id, signal))}>Cancel tablature</button>}
        {["failed", "cancelled"].includes(selectedTask.status) && <button disabled={busy} onClick={() => void action(signal => api.retryTab(selectedTask.id, signal))}>Retry tablature</button>}
        <button disabled={busy} onClick={() => void action(async signal => {
          await api.deleteTab(selectedTask.id, signal); if (!signal.aborted) setSelected("");
        })}>Delete tablature</button>
        {Object.keys(selectedTask.artifacts).map(kind => <a className="button-link" key={kind}
          href={api.tabArtifactUrl(selectedTask.id, kind)}>Download {kind === "alphatex" ? "AlphaTex" : kind.toUpperCase()}</a>)}
      </div>
    </>}
    {page && result && <>
      <div className="artifact-actions" aria-label="Generated tablature navigation">
        <button disabled={pageIndex === 0} onClick={() => goToPage(pageIndex - 1)}>Previous tab page</button>
        <span>Page {pageIndex + 1} / {result.notation_pages.length}</span>
        <button disabled={pageIndex + 1 === result.notation_pages.length} onClick={() => goToPage(pageIndex + 1)}>Next tab page</button>
        {page.measure_seconds.map((time, index) => <button key={time} onClick={() => onSeek(time)}>Seek tab measure {index + 1}</button>)}
      </div>
      {result.limitations.map(text => <p className="form-hint" key={text}>{text}</p>)}
      <AlphaTabView tex={page.tex} cursorFraction={cursor} />
    </>}
  </section>;
}
