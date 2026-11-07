import { useEffect, useRef, useState } from "react";
import { api, type ModelAvailability } from "../lib/api";

const labels = { available: "Available", download_required: "Needs download", unavailable: "Unavailable" };

export default function ModelHealth({ selectedModel }: { selectedModel: string }) {
  const [health, setHealth] = useState<ModelAvailability | null>(null);
  const [error, setError] = useState(false);
  const [checking, setChecking] = useState(true);
  const mounted = useRef(false);
  const inFlight = useRef(false);

  async function check() {
    if (inFlight.current) return;
    inFlight.current = true;
    setChecking(true);
    try {
      const result = await api.modelHealth();
      if (mounted.current) { setHealth(result); setError(false); }
    } catch {
      if (mounted.current) setError(true);
    } finally {
      inFlight.current = false;
      if (mounted.current) setChecking(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    void check();
    const timer = setInterval(() => void check(), 15000);
    return () => { mounted.current = false; clearInterval(timer); };
  }, []);

  return <section className="model-health" aria-label="Model availability">
    <div className="model-health-heading"><h3>Model availability</h3>
      <button type="button" onClick={() => void check()} disabled={checking} aria-label="Refresh model availability">{checking ? "Checking…" : "Refresh"}</button>
    </div>
    <div role="status" aria-live="polite">
      {error ? <p className="model-health-error">Could not check model availability. Try refreshing.</p>
        : !health ? <p className="stage-hint">Checking model files…</p>
        : <ul className="model-health-list">{health.models.map(model => <li key={model.size}>
          <div><strong>{model.size[0].toUpperCase() + model.size.slice(1)}{model.size === selectedModel ? " · selected" : ""}</strong>
            <span className={`model-badge model-${model.status}`}>{labels[model.status]}</span></div>
          <p>{model.status === "available" ? model.source === "configured" ? "Configured local weights" : "HuggingFace cache" : model.message}</p>
        </li>)}</ul>}
    </div>
    <p className="form-hint">File check only—not an inference test. Uncached models download on first use.</p>
  </section>;
}
