"""yt-dlp downloader with pre-download gates. Cancellable via asyncio.Event."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import yt_dlp

from ..models import Job

_MIX_HINTS = ("mix", "megamix", "compilation")


class DownloadRejected(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def _is_mix(info: dict) -> bool:
    title = (info.get("title") or "").lower()
    if "mix" in (info.get("playlist_title") or "").lower():
        return True
    return bool(re.search(r"\b(?:mix|megamix|compilation)\b", title))


async def download_audio(job: Job, dest: Path, cancel: asyncio.Event,
                         progress_cb=None) -> tuple[Path, dict]:
    from .process import run_stage
    result = await run_stage("download", {"job": job.model_dump(mode="json"),
        "dest": str(dest)}, dest.parent, cancel, progress_cb)
    return Path(result["path"]), result["info"]


async def _download_audio(job: Job, dest: Path, cancel: asyncio.Event,
                          progress_cb=None) -> tuple[Path, dict]:
    """Probe gates first (no download), then download audio-only. Returns (file, info)."""
    policy = job.config.yt_dlp
    dest.parent.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest.with_suffix("")) + ".%(ext)s"

    def probe() -> dict:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True,
                               "extract_flat": "in_playlist", "socket_timeout": 20,
                               "js_runtimes": {"node": {}}}) as ydl:
            return ydl.extract_info(job.youtube_url, download=False, process=False) or {}

    info = await asyncio.to_thread(probe)

    if cancel.is_set():
        raise DownloadCancelled("cancelled before download")

    # --- gates ---
    if info.get("_type") == "playlist" or info.get("playlist_count"):
        if not policy.allow_playlist:
            raise DownloadRejected("playlists not allowed")
        if not policy.allow_mix:
            raise DownloadRejected("mixes not allowed")
    if _is_mix(info) and not policy.allow_mix:
        raise DownloadRejected("mixes not allowed")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise DownloadRejected("live streams not allowed")
    duration = info.get("duration")
    if duration is None:
        raise DownloadRejected("cannot enforce duration limit: duration unknown")
    if duration > policy.max_duration_s:
        raise DownloadRejected(f"duration {duration}s exceeds limit {policy.max_duration_s}s")

    def hook(d: dict):
        if cancel.is_set():
            raise DownloadCancelled("cancelled during download")
        if progress_cb and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            if total:
                progress_cb(min(1.0, done / total))

    def run() -> dict:
        opts = {
            "quiet": True, "no_warnings": True,
            "socket_timeout": 20,
            "js_runtimes": {"node": {}},
            "format": policy.format,
            "noplaylist": not policy.allow_playlist,
            "outtmpl": outtmpl,
            "progress_hooks": [hook],
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(job.youtube_url, download=True)
            if result and result.get("vcodec") not in (None, "none"):
                raise DownloadRejected("selected format includes video; choose audio-only")
        # find produced file
        for ext in ("m4a", "mp3", "opus", "webm", "mp4"):
            c = dest.with_suffix(f".{ext}")
            if c.exists():
                return {"file": c, "info": info}
        raise RuntimeError("yt-dlp produced no supported audio file")

    res = await asyncio.to_thread(run)
    return res["file"], res["info"]
