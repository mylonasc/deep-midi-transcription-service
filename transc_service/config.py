"""Server defaults (env-overridable). Per-job config merges over these."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ServerConfig:
    data_dir: str = os.environ.get("DATA_DIR", "/data")
    workers: int = int(os.environ.get("WORKERS", "1"))
    default_model: str = os.environ.get("DEFAULT_MODEL", "medium")
    default_max_duration_s: int = int(os.environ.get("MAX_DURATION_S", "600"))

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "meta.db")

    @property
    def objects_root(self) -> str:
        return os.path.join(self.data_dir, "objects")
