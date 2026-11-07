"""Job / config models. JobConfig is versioned and forward-compatible."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    TRANSCRIBING = "transcribing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}


class YtDlpPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = "bestaudio[abr<=128]/worstaudio"
    max_duration_s: int = Field(default=600, gt=0)
    # One job produces one song. Playlist fan-out is not implemented yet.
    allow_playlist: Literal[False] = False
    allow_mix: bool = False
    allow_live: Literal[False] = False


class TranscriptionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["small", "medium", "large"] = "medium"
    detect_tempo: Literal["best-effort", True, False] = "best-effort"  # type: ignore[valid-type]
    instruments: list[str] | None = None
    batch_size: int | None = Field(default=None, ge=1)
    beam_size: int = Field(default=1, ge=1)
    prelude_forcing: bool = True

    @model_validator(mode="after")
    def validate_batching(self):
        if self.prelude_forcing and self.batch_size and self.batch_size > 1:
            raise ValueError("batch_size > 1 requires prelude_forcing=false")
        return self


class JobConfig(BaseModel):
    """Versioned per-job config. `extensions` absorbs future knobs without migration."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    yt_dlp: YtDlpPolicy = Field(default_factory=YtDlpPolicy)
    transcription: TranscriptionOptions = Field(default_factory=TranscriptionOptions)
    extensions: dict = Field(default_factory=dict)


class JobProgress(BaseModel):
    download_progress: float | None = None  # 0..1
    chunks_completed: int | None = None
    chunks_total: int | None = None


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    youtube_url: str
    status: JobStatus = JobStatus.QUEUED
    config: JobConfig = Field(default_factory=JobConfig)
    progress: JobProgress = Field(default_factory=JobProgress)
    title: str | None = None
    duration_s: float | None = None
    audio_key: str | None = None
    midi_key: str | None = None
    events_key: str | None = None
    error: str | None = None
    retry_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)
