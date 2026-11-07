"""SQLite MetadataStore (aiosqlite). Single table, JSON-encoded config/progress."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from ..models import Job, JobStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  youtube_url TEXT NOT NULL,
  status TEXT NOT NULL,
  config_json TEXT NOT NULL,
  progress_json TEXT NOT NULL,
  title TEXT,
  duration_s REAL,
  audio_key TEXT,
  midi_key TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        youtube_url=row["youtube_url"],
        status=JobStatus(row["status"]),
        config=json.loads(row["config_json"]),
        progress=json.loads(row["progress_json"]),
        title=row["title"],
        duration_s=row["duration_s"],
        audio_key=row["audio_key"],
        midi_key=row["midi_key"],
        events_key=row["events_key"],
        error=row["error"],
        retry_count=row["retry_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteMetadataStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_SCHEMA)
            async with db.execute("PRAGMA table_info(jobs)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
            if "events_key" not in columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN events_key TEXT")
            if "retry_count" not in columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
            await db.execute("PRAGMA journal_mode=WAL")
            await db.commit()

    async def create(self, job: Job) -> Job:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """INSERT INTO jobs (id,youtube_url,status,config_json,progress_json,
                title,duration_s,audio_key,midi_key,error,created_at,updated_at,events_key,retry_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id, job.youtube_url, job.status.value,
                    job.config.model_dump_json(), job.progress.model_dump_json(),
                    job.title, job.duration_s, job.audio_key, job.midi_key,
                    job.error, job.created_at.isoformat(), job.updated_at.isoformat(),
                    job.events_key,
                    job.retry_count,
                ),
            )
            await db.commit()
        return job

    async def get(self, job_id: str) -> Job | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
                row = await cur.fetchone()
                return _row_to_job(row) if row else None

    async def list(self, status: JobStatus | None = None, limit: int = 100, offset: int = 0) -> list[Job]:
        q = "SELECT * FROM jobs"
        args: list = []
        if status is not None:
            q += " WHERE status=?"
            args.append(status.value)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(q, args) as cur:
                return [_row_to_job(r) async for r in cur]

    async def update(self, job: Job) -> Job:
        job.touch()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE jobs SET status=?, config_json=?, progress_json=?, title=?,
                   duration_s=?, audio_key=?, midi_key=?, error=?, updated_at=?, events_key=?, retry_count=? WHERE id=?""",
                (
                    job.status.value, job.config.model_dump_json(),
                    job.progress.model_dump_json(), job.title, job.duration_s,
                    job.audio_key, job.midi_key, job.error,
                    job.updated_at.isoformat(), job.events_key, job.retry_count, job.id,
                ),
            )
            await db.commit()
        return job

    async def delete(self, job_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            await db.commit()
