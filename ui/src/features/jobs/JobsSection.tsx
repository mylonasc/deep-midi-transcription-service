import JobCard, { ACTIVE } from "../../components/JobCard";
import EmptyState from "../../components/ui/EmptyState";
import type { Job } from "../../lib/api";

/** Summary counts, status filter, and job cards. Pure view over jobs. */
export default function JobsSection({ jobs, loaded, filter, onFilter, busy, onAction, onPlay }: {
  jobs: Job[];
  loaded: boolean;
  filter: string;
  onFilter: (filter: string) => void;
  busy: Set<string>;
  onAction: (id: string, operation: () => Promise<unknown>) => void;
  onPlay: (id: string) => void;
}) {
  const active = jobs.filter(job => ACTIVE.has(job.status)).length;
  const completed = jobs.filter(job => job.status === "done").length;
  const failed = jobs.filter(job => job.status === "failed").length;
  const visible = jobs.filter(job => filter === "all" || (filter === "active" ? ACTIVE.has(job.status) : job.status === filter));

  return <>
    <div className="summary-grid" aria-label="Job summary">
      <div><strong>{active}</strong><span>In progress</span></div>
      <div><strong>{completed}</strong><span>Complete</span></div>
      <div><strong>{failed}</strong><span>Need attention</span></div>
    </div>
    <section className="jobs-section" aria-labelledby="jobs-heading">
      <div className="section-heading"><div><p className="eyebrow">WORKSPACE</p><h2 id="jobs-heading">Jobs <span className="count">{jobs.length}</span></h2></div>
        <div className="filter-control"><label htmlFor="status-filter">Show</label><select id="status-filter" value={filter} onChange={event => onFilter(event.target.value)}>
          <option value="all">All jobs</option><option value="active">In progress</option><option value="done">Complete</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option>
        </select></div>
      </div>
      <p className="live-caption">Status refreshes every 2 seconds</p>
      {!loaded ? <div className="empty-state" role="status">Loading jobs…</div>
        : visible.length === 0 ? <EmptyState title={jobs.length ? "No matching jobs" : "Your queue is ready"}>{jobs.length ? "Choose another status to see more jobs." : "Add a YouTube link to create your first transcription."}</EmptyState>
        : <div className="job-list">{visible.map(job => <JobCard key={job.id} job={job} busy={busy.has(job.id)} onAction={(id, operation) => void onAction(id, operation)} onPlay={onPlay} />)}</div>}
    </section>
  </>;
}
