import { useEffect, useMemo, useState } from "react";
import { buildAlphaTex, type NotationMode } from "./alphaTex";
import { barStartSeconds, pageEndSeconds, pageStartSeconds, timeToPage } from "./playback";
import { useMidiPlayback } from "./useMidiPlayback";
import type { MidiSong } from "./types";

/**
 * Notation-view state: track/mode/page selection, shared playback wiring,
 * page auto-follow, and cursor fraction. Keeps SongViews a thin composition.
 */
export function useVisualizationState(song: MidiSong) {
  const [view, setView] = useState("notation");
  const [trackId, setTrackId] = useState(song.tracks.find(track => !track.percussion)?.id ?? "");
  const [mode, setMode] = useState<NotationMode | "auto">("auto");
  const [page, setPage] = useState(0);
  const [follow, setFollow] = useState(true);
  const playback = useMidiPlayback(song);
  const playbackCurrent = playback.current;
  const playbackPlaying = playback.playing;

  const track = song.tracks.find(item => item.id === trackId);
  const resolvedMode: NotationMode = mode === "auto"
    ? track && track.program >= 24 && track.program < 32 ? "guitar"
      : track && track.program >= 32 && track.program < 40 ? "bass" : "score"
    : mode;
  const notation = useMemo(() => track && !track.percussion
    ? buildAlphaTex(song, track, resolvedMode, page) : null, [song, track, resolvedMode, page]);

  const pageStart = pageStartSeconds(song, page);
  const pageEnd = pageEndSeconds(song, page);

  useEffect(() => {
    if (!follow || !playbackPlaying) return;
    const next = timeToPage(song, playbackCurrent);
    if (next !== page) setPage(next);
  }, [follow, playbackPlaying, playbackCurrent, song, page]);

  const goToPage = (next: number) => {
    const pages = notation?.pages ?? 1;
    const clamped = Math.min(Math.max(0, next), pages - 1);
    setPage(clamped);
    playback.seek(pageStartSeconds(song, clamped));
  };

  const selectTrack = (id: string) => { setTrackId(id); setPage(0); };
  const seekMeasure = (bar: number) => playback.seek(barStartSeconds(song, page, bar));
  const cursorFraction = playbackCurrent < pageStart || playbackCurrent > pageEnd || pageEnd <= pageStart
    ? null : (playbackCurrent - pageStart) / (pageEnd - pageStart);

  return {
    view, setView, trackId, selectTrack, mode, setMode, page, goToPage,
    follow, setFollow, track, resolvedMode, notation, cursorFraction, playback, seekMeasure,
  };
}
