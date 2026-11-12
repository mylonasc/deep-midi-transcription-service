"""SQLite child-task persistence; JSON schema evolves independently of jobs."""
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .tasks import TabTask


class SqliteTablatureStore:
    def __init__(self, db_path):
        self.db_path = db_path

    async def init(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS tablature_tasks (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)""")
            await db.execute("CREATE INDEX IF NOT EXISTS tablature_parent ON tablature_tasks(job_id)")
            await db.execute("PRAGMA journal_mode=WAL")
            await db.commit()

    async def create(self, task):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO tablature_tasks VALUES (?,?,?,?)",
                             (task.id, task.job_id, task.created_at.isoformat(), task.model_dump_json()))
            await db.commit()

    async def get(self, task_id):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT payload FROM tablature_tasks WHERE id=?", (task_id,)) as cursor:
                row = await cursor.fetchone()
                return TabTask.model_validate_json(row[0]) if row else None

    async def list(self, job_id=None):
        async with aiosqlite.connect(self.db_path) as db:
            query, args = "SELECT payload FROM tablature_tasks", ()
            if job_id is not None:
                query, args = query + " WHERE job_id=?", (job_id,)
            async with db.execute(query + " ORDER BY created_at, id", args) as cursor:
                return [TabTask.model_validate_json(row[0]) for row in await cursor.fetchall()]

    async def update(self, task):
        task.updated_at = datetime.now(timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE tablature_tasks SET payload=? WHERE id=?", (task.model_dump_json(), task.id))
            await db.commit()

    async def delete(self, task_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM tablature_tasks WHERE id=?", (task_id,))
            await db.commit()
