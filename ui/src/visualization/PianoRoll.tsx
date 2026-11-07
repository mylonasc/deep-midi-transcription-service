import { useEffect, useState } from "react";
import type { MidiSong } from "./types";

const colors = ["#6043d6", "#087f8c", "#ba4a24", "#256c3d", "#ab3389", "#6d630c"];

interface PianoRollProps {
  song: MidiSong;
  currentTime?: number;
  playing?: boolean;
  onSeek?: (time: number) => void;
}

/** Original MIDI timing across all tracks; no fretting/alphaTab dependencies. */
export default function PianoRoll({ song, currentTime, playing = false, onSeek }: PianoRollProps) {
  const [trackId, setTrackId] = useState("all");
  const [start, setStart] = useState(0);
  const [follow, setFollow] = useState(true);
  const windowSize = 20;
  const maxStart = Math.max(0, song.duration - windowSize);
  const clampedStart = Math.min(Math.max(0, start), maxStart);
  const end = Math.min(song.duration || windowSize, clampedStart + windowSize);
  const tracks = song.tracks.filter(track => trackId === "all" || track.id === trackId);
  const notes = tracks.flatMap(track => track.notes);
  const low = Math.max(0, notes.reduce((min, note) => Math.min(min, note.pitch), 48) - 2);
  const high = Math.min(127, notes.reduce((max, note) => Math.max(max, note.pitch), 72) + 2);
  const rowHeight = 9;
  const height = (high - low + 1) * rowHeight + 24;
  const x = (time: number) => 42 + ((time - clampedStart) / windowSize) * 900;

  useEffect(() => {
    if (!follow || !playing || currentTime === undefined) return;
    if (currentTime < clampedStart || currentTime >= clampedStart + windowSize) {
      setStart(Math.min(maxStart, Math.max(0, currentTime - windowSize * 0.25)));
    }
  }, [follow, playing, currentTime, clampedStart, maxStart, windowSize]);

  const seekFromClick = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!onSeek) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const svgX = ((event.clientX - rect.left) / rect.width) * 950;
    const time = clampedStart + ((svgX - 42) / 900) * windowSize;
    onSeek(Math.min(Math.max(0, time), song.duration));
  };

  const playheadVisible = currentTime !== undefined && currentTime >= clampedStart && currentTime <= clampedStart + windowSize;
  const isActive = (time: number, duration: number) =>
    currentTime !== undefined && time <= currentTime && time + duration > currentTime;

  return <div>
    <label htmlFor="roll-track">Piano-roll tracks</label>
    <select id="roll-track" value={trackId} onChange={event => setTrackId(event.target.value)}><option value="all">All instruments</option>
      {song.tracks.map(track => <option key={track.id} value={track.id}>{track.name} · {track.instrument}</option>)}
    </select>
    <div className="roll-legend">{tracks.map((track, i) => <span key={track.id}><i style={{ background: colors[i % colors.length] }} />{track.name}{track.percussion ? " (drums)" : ""}</span>)}</div>
    <div className="piano-roll-scroll" role="region" aria-label="Piano roll" tabIndex={0}>
      <svg viewBox={`0 0 950 ${height}`} width="950" height={height} role="img"
        aria-label={`MIDI piano roll, ${clampedStart.toFixed(1)} to ${end.toFixed(1)} seconds`}
        onClick={onSeek ? seekFromClick : undefined} style={onSeek ? { cursor: "pointer" } : undefined}>
        <title>Original MIDI notes — time horizontally, MIDI pitch vertically{onSeek ? ". Activate to seek playback." : ""}</title>
        {Array.from({ length: high - low + 1 }, (_, i) => {
          const pitch = high - i;
          return <g key={pitch}><rect x={42} y={i * rowHeight + 24} width={900} height={rowHeight} fill={[1, 3, 6, 8, 10].includes(pitch % 12) ? "#eef0f7" : "#fff"} />
            {pitch % 12 === 0 && <text x={4} y={i * rowHeight + 32} fontSize={9} fill="#626a80">C{Math.floor(pitch / 12) - 1}</text>}</g>;
        })}
        {Array.from({ length: 11 }, (_, i) => <g key={i}><line x1={x(clampedStart + i * 2)} x2={x(clampedStart + i * 2)} y1={20} y2={height} stroke="#d7dce9" />
          <text x={x(clampedStart + i * 2)} y={12} fontSize={10}>{(clampedStart + i * 2).toFixed(0)}s</text></g>)}
        {tracks.flatMap((track, i) => track.notes.filter(note => note.time < clampedStart + windowSize && note.time + note.duration > clampedStart).map(note => <rect
          key={note.id} data-testid="roll-note" x={x(Math.max(clampedStart, note.time))} y={(high - note.pitch) * rowHeight + 24}
          width={Math.max(2, x(Math.min(clampedStart + windowSize, note.time + note.duration)) - x(Math.max(clampedStart, note.time)))} height={7}
          rx={2} fill={colors[i % colors.length]} opacity={currentTime === undefined || isActive(note.time, note.duration) ? 1 : 0.75}
          stroke={isActive(note.time, note.duration) ? "#1f1b3d" : "none"} strokeWidth={isActive(note.time, note.duration) ? 1.5 : 0}>
          <title>{track.name}: MIDI {note.pitch}, {note.time.toFixed(2)}s, {note.duration.toFixed(2)}s</title></rect>))}
        {playheadVisible && <line data-testid="roll-playhead" x1={x(currentTime as number)} x2={x(currentTime as number)}
          y1={16} y2={height} stroke="#c02848" strokeWidth={2} />}
      </svg>
    </div>
    {onSeek && <label className="checkbox-label"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} />Follow playback</label>}
    <label htmlFor="roll-position">Time window: {clampedStart.toFixed(1)}–{end.toFixed(1)} seconds</label>
    <input id="roll-position" type="range" min={0} max={maxStart} step={0.1} value={clampedStart} onChange={event => setStart(Number(event.target.value))} />
    <p className="form-hint">Original timing, including drums and notes outside guitar range. Scroll within the roll on small screens. This is independent of the notation selection.{onSeek ? " Select the roll timeline to seek the shared preview playback." : ""}</p>
  </div>;
}
