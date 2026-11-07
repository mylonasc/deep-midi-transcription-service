import { formatPlaybackTime } from "./playback";

/** Shared play/pause/restart/slider transport. Drives roll and tabs in sync. */
export default function Transport({ current, duration, playing, error, onToggle, onRestart, onSeek }: {
  current: number; duration: number; playing: boolean; error: string | null;
  onToggle: () => void; onRestart: () => void; onSeek: (time: number) => void;
}) {
  return <div className="transport" aria-label="Synchronized preview playback">
    <div className="transport-controls">
      <button onClick={onToggle} disabled={!(duration > 0)}>{playing ? "Pause" : "Play"}</button>
      <button onClick={onRestart} disabled={!(duration > 0)}>Restart</button>
      <span role="status">{formatPlaybackTime(current)} / {formatPlaybackTime(duration)}</span>
    </div>
    <input aria-label="Visualization playback position" type="range" min={0} max={Math.max(0, duration)}
      step={0.05} value={Math.min(current, Math.max(0, duration))}
      onChange={event => onSeek(Number(event.target.value))} style={{ width: "100%" }} />
    {error && <p role="alert" className="notice">{error}</p>}
    <p className="form-hint">One preview engine drives both views. Play from either tab; the piano-roll playhead and notation page follow the same position.</p>
  </div>;
}
