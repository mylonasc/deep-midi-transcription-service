/** Jobs/Visualization tab switcher with arrow-key navigation. */
export default function WorkspaceTabs({ tab, onChange }: {
  tab: string;
  onChange: (tab: string) => void;
}) {
  return <div className="app-tabs" role="tablist" aria-label="Workspace" onKeyDown={event => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? "jobs" : event.key === "End" ? "visualization" : tab === "jobs" ? "visualization" : "jobs";
      onChange(next); document.getElementById(`tab-${next}`)?.focus();
    }
  }}>
    <button id="tab-jobs" role="tab" aria-selected={tab === "jobs"} aria-controls="panel-jobs" tabIndex={tab === "jobs" ? 0 : -1} onClick={() => onChange("jobs")}>Jobs & artifacts</button>
    <button id="tab-visualization" role="tab" aria-selected={tab === "visualization"} aria-controls="panel-visualization" tabIndex={tab === "visualization" ? 0 : -1} onClick={() => onChange("visualization")}>Visualization</button>
  </div>;
}
