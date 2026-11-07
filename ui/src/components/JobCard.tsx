import { api, type Job } from "../lib/api";

export const ACTIVE = new Set(["queued", "downloading", "downloaded", "transcribing"]);
const labels: Record<string, string> = {
  queued: "Queued", downloading: "Downloading", downloaded: "Downloaded",
  transcribing: "Transcribing", done: "Complete", failed: "Failed", cancelled: "Cancelled",
};

export default function JobCard({ job, busy, onAction, onPlay }: {
  job: Job; busy: boolean;
  onAction: (id: string, operation: () => Promise<unknown>) => void;
  onPlay: (id: string) => void;
}) {
  const p = job.progress;
  const chunkTotal = p.chunks_total ?? 0;
  const fraction = job.status === "transcribing" && chunkTotal > 0
    ? (p.chunks_completed ?? 0) / chunkTotal
    : job.status === "downloading" ? p.download_progress : null;
  const percent = fraction == null ? null : Math.round(Math.max(0, Math.min(1, fraction)) * 100);

  return <article className="job-card" data-testid="job-card" aria-labelledby={`title-${job.id}`}>
    <div className="job-heading">
      <h3 id={`title-${job.id}`}>{job.title || "YouTube transcription"}</h3>
      <span className={`status status-${job.status}`}>{labels[job.status]}</span>
    </div>
    <a className="source-link" href={job.youtube_url} target="_blank" rel="noreferrer">{job.youtube_url}</a>
    <div className="job-meta">
      <code>{job.id}</code>
      <span>{job.config.transcription?.model || "medium"} model</span>
      <span>Attempt {(job.retry_count ?? 0) + 1}</span>
    </div>
    {percent != null && <div className="job-progress">
      <div><span>{job.status === "downloading" ? "Downloading audio" : `Processing chunk ${p.chunks_completed ?? 0} of ${chunkTotal}`}</span><strong>{percent}%</strong></div>
      <progress aria-label={`${job.status} progress`} value={percent} max={100} />
    </div>}
    {job.status === "queued" && <p className="stage-hint">Waiting for an available worker{job.audio_key ? " · downloaded audio will be reused" : ""}.</p>}
    {job.status === "transcribing" && percent == null && <p className="stage-hint">Preparing audio or loading the model…</p>}
    {job.error && <details className="error-details"><summary>Error details</summary><pre>{job.error}</pre></details>}
    <div className="job-actions" aria-label="Job actions">
      {(job.status === "failed" || job.status === "cancelled") && <button className="button-primary" disabled={busy} onClick={() => onAction(job.id, () => api.retry(job.id))}>Retry</button>}
      {ACTIVE.has(job.status) && <button disabled={busy} onClick={() => onAction(job.id, () => api.cancel(job.id))}>Cancel</button>}
      {job.midi_key && <><button onClick={() => onPlay(job.id)}>Preview MIDI</button><a className="button-link" href={api.midiUrl(job.id)}>Download MIDI</a></>}
      {job.audio_key && <a className="button-link" href={api.audioUrl(job.id)}>Open audio</a>}
      <button className="button-danger" disabled={busy} onClick={() => {
        if (window.confirm("Delete this job and all its artifacts?")) onAction(job.id, () => api.remove(job.id));
      }}>Delete</button>
      {busy && <span className="stage-hint" role="status">Updating…</span>}
    </div>
  </article>;
}
