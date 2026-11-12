import { useState } from "react";
import ModelHealth from "../../components/ModelHealth";
import { api, type Job } from "../../lib/api";

/** New-transcription form. Reports the created job; parent refreshes + selects. */
export default function QueueForm({ onQueued }: { onQueued: (job: Job) => void }) {
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("medium");
  const [maxDur, setMaxDur] = useState(600);
  const [allowMix, setAllowMix] = useState(false);
  const [autoTab, setAutoTab] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      onQueued(await api.create(url.trim(), model, maxDur, allowMix, autoTab));
      setUrl("");
    } catch (reason) { setError(String(reason)); }
    finally { setSubmitting(false); }
  }

  return <section className="panel queue-panel" aria-labelledby="queue-heading">
    <p className="eyebrow">NEW TRANSCRIPTION</p><h2 id="queue-heading">Queue a song</h2>
    <p className="section-copy">Paste a video link. We’ll download the audio and create your MIDI.</p>
    {error && <div className="notice" role="alert"><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}
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
      <label className="checkbox-label"><input type="checkbox" checked={autoTab} onChange={event => setAutoTab(event.target.checked)} />Generate guitar tablature automatically when one eligible guitar part is found</label>
      <ModelHealth selectedModel={model} />
      <button className="button-primary submit-button" type="submit" disabled={submitting || !url.trim() || !Number.isFinite(maxDur) || maxDur < 1}>{submitting ? "Queuing…" : "Queue transcription"}</button>
      <p className="form-hint">One video per job. Larger models need more time and memory.</p>
    </form>
  </section>;
}
