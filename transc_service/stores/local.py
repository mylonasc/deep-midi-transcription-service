"""Local-filesystem ObjectStore. Keys are S3-shaped: jobs/{id}/..."""
from __future__ import annotations

import shutil
from pathlib import Path


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if self._root.resolve() not in p.parents and p != self._root.resolve():
            raise ValueError(f"unsafe key: {key}")
        return p

    async def put_bytes(self, key: str, data: bytes) -> None:
        p = self._resolve(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    async def put_path(self, key: str, src: Path) -> None:
        p = self._resolve(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)

    async def get_bytes(self, key: str) -> bytes | None:
        p = self._resolve(key)
        return p.read_bytes() if p.exists() else None

    async def get_path(self, key: str) -> Path | None:
        p = self._resolve(key)
        return p if p.exists() else None

    async def delete_prefix(self, prefix: str) -> None:
        base = self._resolve(prefix)
        if base.is_dir():
            shutil.rmtree(base, ignore_errors=True)
        elif base.exists():
            base.unlink(missing_ok=True)
