import { useCallback, useEffect, useState } from "react";
import MidiPlayer from "./components/MidiPlayer";
import { api, type Job } from "./lib/api";

const ACTIVE = new Set(["queued", "downloading", "downloaded", "transcribing"]);

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("medium");
  const [maxDur, setMaxDur] = useState(600);
  const [allowMix, setAllowMix] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setJobs(await api.list());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
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

  const submit = async () => {
    setErr(null);
    setSubmitting(true);
    try {
      const job: Job = await api.create(url, model, maxDur, allowMix);
      setUrl("");
      setSelected(job.id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const action = async (operation: () => Promise<unknown>) => {
    try { await operation(); await refresh(); }
    catch (error) { setErr(String(error)); }
  };

  const queued = jobs.filter((j) => j.status === "queued" || j.status === "downloading");
  const downloaded = jobs.filter((j) => j.audio_key);
  const transcribed = jobs.filter((j) => j.midi_key);
  const sel = jobs.find((j) => j.id === selected) ?? transcribed[0];

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 16, fontFamily: "sans-serif" }}>
      <h1>Transcription Service</h1>

      <section style={{ border: "1px solid #ddd", padding: 12, borderRadius: 8 }}>
        <h2>Queue a job</h2>
        <input
          placeholder="https://youtube.com/watch?v=…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          style={{ width: "100%", padding: 8 }}
        />
        <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
          <label>
            Model{" "}
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="small">small</option>
              <option value="medium">medium</option>
              <option value="large">large</option>
            </select>
          </label>
          <label>
            Max duration (s){" "}
            <input
              type="number"
              min={1}
              value={maxDur}
              onChange={(e) => setMaxDur(Number(e.target.value))}
              style={{ width: 90 }}
            />
          </label>
          <label>
            <input type="checkbox" checked={allowMix} onChange={(e) => setAllowMix(e.target.checked)} />{" "}
            allow mixes
          </label>
          <button onClick={submit} disabled={submitting || !url.trim() || maxDur < 1}>
            {submitting ? "Queuing…" : "Start / Queue"}
          </button>
        </div>
        {err && <p style={{ color: "red" }}>{err}</p>}
      </section>

      <section>
        <h2>Jobs ({jobs.length}) — live</h2>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th align="left">id</th>
              <th align="left">title/url</th>
              <th align="left">status</th>
              <th align="left">progress</th>
              <th align="left">actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id} style={{ borderTop: "1px solid #eee" }}>
                <td><code>{j.id}</code></td>
                <td>{j.title ?? j.youtube_url}</td>
                <td>
                  <span style={{ padding: "2px 8px", borderRadius: 12, background: j.status === "done" ? "#cfc" : j.status === "failed" ? "#fcc" : "#eee" }}>
                    {j.status}
                  </span>
                  {j.error && <div style={{ color: "red", fontSize: 12 }}>{j.error}</div>}
                </td>
                <td style={{ fontSize: 12 }}>
                  {j.progress.download_progress != null && <div>dl {(j.progress.download_progress * 100).toFixed(0)}%</div>}
                  {j.progress.chunks_total != null && <div>chunks {j.progress.chunks_completed}/{j.progress.chunks_total}</div>}
                </td>
                <td style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {ACTIVE.has(j.status) && <button onClick={() => void action(() => api.cancel(j.id))}>Cancel</button>}
                  <button onClick={() => { if (window.confirm("Delete this job and all its artifacts?")) void action(() => api.remove(j.id)); }}>Delete</button>
                  {j.status === "done" && <button onClick={() => setSelected(j.id)}>Play</button>}
                  {j.status === "done" && <a href={api.midiUrl(j.id)}>midi</a>}
                  {j.audio_key && (
                    <a href={api.audioUrl(j.id)}>audio</a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="artifact-grid">
        <div><h3>Queued ({queued.length})</h3><ul>{queued.map((j) => <li key={j.id}><code>{j.id}</code> {j.status}</li>)}</ul></div>
        <div><h3>Downloaded artifacts ({downloaded.length})</h3><ul>{downloaded.map((j) => <li key={j.id}><code>{j.id}</code> <a href={api.audioUrl(j.id)}>audio</a></li>)}</ul></div>
        <div><h3>Transcriptions ({transcribed.length})</h3><ul>{transcribed.map((j) => <li key={j.id}><code>{j.id}</code> <a href={api.midiUrl(j.id)}>midi</a> <button onClick={() => setSelected(j.id)}>play</button></li>)}</ul></div>
      </section>

      {sel && sel.status === "done" && (
        <section style={{ marginTop: 16 }}>
          <h2>Player</h2>
          <MidiPlayer midiUrl={api.midiUrl(sel.id)} title={sel.title ?? sel.id} />
        </section>
      )}
    </div>
  );
}
