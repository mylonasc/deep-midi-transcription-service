import { useEffect, useRef, useState } from "react";
import { AlphaTabApi, FontFileFormat } from "@coderline/alphatab";
import fontUrl from "@coderline/alphatab/font/Bravura.woff2?url";
import fontLicenseUrl from "@coderline/alphatab/font/Bravura-OFL.txt?url";

/** Rendering only: does not parse MIDI or choose string/fret positions. */
export default function AlphaTabView({ tex, cursorFraction }: { tex: string; cursorFraction?: number | null }) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!host.current) return;
    setReady(false); setError(null);
    const api = new AlphaTabApi(host.current, {
      core: { useWorkers: false, engine: "svg", enableLazyLoading: false,
        smuflFontSources: new Map([[FontFileFormat.Woff2, fontUrl]]) },
      player: { enablePlayer: false }, display: { scale: 0.85 },
    });
    api.renderFinished.on(() => setReady(true));
    api.error.on(reason => setError(String(reason)));
    try { api.tex(tex); } catch (reason) { setError(String(reason)); }
    return () => api.destroy();
  }, [tex]);
  const clamped = cursorFraction === undefined || cursorFraction === null
    ? null : Math.min(1, Math.max(0, cursorFraction));
  return <>
    {error && <p role="alert">Notation could not be rendered: {error}</p>}
    {!ready && !error && <p role="status">Rendering notation…</p>}
    <div className="notation-scroll" role="region" aria-label="alphaTab notation" tabIndex={0}>
      <div className="notation-frame">
        {clamped !== null && <div data-testid="notation-playhead" className="notation-cursor" aria-hidden="true" style={{ left: `${(clamped * 100).toFixed(2)}%` }} />}
        <div ref={host} data-testid="alphatab-score" />
      </div>
    </div>
    <p className="form-hint">Rendered by <a href="https://github.com/CoderLine/alphaTab" target="_blank" rel="noreferrer">alphaTab (MPL-2.0)</a> · <a href={fontLicenseUrl} target="_blank" rel="noreferrer">Bravura font license</a></p>
  </>;
}
