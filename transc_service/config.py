"""Server defaults (env-overridable). Per-job config merges over these."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    data_dir: str = field(default_factory=lambda: os.environ.get("DATA_DIR", "/data"))
    workers: int = field(default_factory=lambda: int(os.environ.get("WORKERS", "1")))
    default_model: str = field(default_factory=lambda: os.environ.get("DEFAULT_MODEL", "medium"))
    default_max_duration_s: int = field(default_factory=lambda: int(os.environ.get("MAX_DURATION_S", "600")))
    weights_small: str = field(default_factory=lambda: os.environ.get("MUSCRIPTOR_WEIGHTS_SMALL", ""))
    weights_medium: str = field(default_factory=lambda: os.environ.get("MUSCRIPTOR_WEIGHTS_MEDIUM", ""))
    weights_large: str = field(default_factory=lambda: os.environ.get("MUSCRIPTOR_WEIGHTS_LARGE", ""))
    tab_model_dir: str = field(default_factory=lambda: os.environ.get("OPEN_FRET_MODEL_DIR", ""))
    tab_model_revision: str = field(default_factory=lambda: os.environ.get("OPEN_FRET_MODEL_REVISION", "b8edf84f4b76a8bc03bc1d78255c77364fba72eb"))
    tab_allow_download: bool = field(default_factory=lambda: os.environ.get("OPEN_FRET_ALLOW_DOWNLOAD", "false").lower() == "true")
    tab_device: str = field(default_factory=lambda: os.environ.get("OPEN_FRET_DEVICE", "cpu"))
    tab_timeout_s: int = field(default_factory=lambda: int(os.environ.get("OPEN_FRET_TIMEOUT_S", "300")))
    tab_max_notes: int = field(default_factory=lambda: int(os.environ.get("OPEN_FRET_MAX_NOTES", "20000")))

    def weights_for(self, size: str) -> str:
        """Resolve server-owned weights without accepting arbitrary paths from jobs."""
        if size not in {"small", "medium", "large"}:
            raise ValueError(f"unsupported model size: {size}")
        configured = getattr(self, f"weights_{size}")
        if not configured:
            return size  # muscriptor's normal HuggingFace download/cache resolution
        path = Path(configured).expanduser()
        if not path.is_file():
            raise ValueError(f"Configured {size} weights not found: {path}")
        if not path.with_name("config.json").is_file():
            raise ValueError(f"Model config.json must be next to configured weights: {path}")
        return str(path)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "meta.db")

    @property
    def objects_root(self) -> str:
        return os.path.join(self.data_dir, "objects")
