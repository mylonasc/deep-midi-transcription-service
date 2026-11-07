import { api, type Job } from "../lib/api";

export default function ArtifactList({ jobs, onPlay }: { jobs: Job[]; onPlay: (id: string) => void }) {
  const groups = [
    { title: "Queued", jobs: jobs.filter(job => job.status === "queued" || job.status === "downloading"), kind: "queued" },
    { title: "Downloaded artifacts", jobs: jobs.filter(job => job.audio_key), kind: "audio" },
    { title: "Transcriptions", jobs: jobs.filter(job => job.midi_key), kind: "midi" },
  ];
  return <section aria-label="Artifact library" className="artifact-grid">
    {groups.map(group => <div className="panel artifact-panel" key={group.kind}>
      <h2>{group.title} ({group.jobs.length})</h2>
      {group.jobs.length === 0 ? <p className="empty-hint">{group.kind === "queued" ? "Nothing waiting." : "No artifacts yet."}</p>
        : <ul className="artifact-list">{group.jobs.map(job => <li key={job.id}>
          <span className="artifact-title">{job.title || job.id}</span>
          <div className="artifact-actions">
            {group.kind === "queued" ? <span className="stage-hint">{job.status}</span>
              : <a className="button-link" href={group.kind === "audio" ? api.audioUrl(job.id) : api.midiUrl(job.id)}>{group.kind === "audio" ? "Open audio" : "Download MIDI"}</a>}
            {group.kind === "midi" && <button onClick={() => onPlay(job.id)}>Preview</button>}
          </div>
        </li>)}</ul>}
    </div>)}
  </section>;
}
