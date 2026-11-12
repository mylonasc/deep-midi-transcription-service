"""Offline availability checks only; never load a model or download files."""
import importlib.util
import json
from pathlib import Path
import re
import shutil

from .adapter import MODEL_ID, SOURCE_REVISION


def validator_available():
    validator = Path(__file__).resolve().parents[2] / "tools" / "notation"
    return bool(shutil.which("node")) and (validator / "node_modules" / "@coderline" / "alphatab").is_dir()


def model_health(cfg):
    base = {"model_id": MODEL_ID, "model_revision": cfg.tab_model_revision,
            "source_revision": SOURCE_REVISION, "scope": "File availability only, not tested inference or licensing clearance."}
    try:
        if cfg.tab_device not in {"cpu", "cuda"} or not re.fullmatch(r"[0-9a-f]{40}", cfg.tab_model_revision):
            raise ValueError("Invalid tablature device or unpinned model revision")
        if cfg.tab_timeout_s <= 0 or cfg.tab_max_notes <= 0:
            raise ValueError("Tablature timeout and note limit must be positive")
        spec = importlib.util.find_spec("open_fret")
        if spec is None or not spec.origin or Path(spec.origin).with_name("SOURCE_REVISION").read_text().strip() != SOURCE_REVISION:
            raise ValueError("Pinned Open-Fret inference runtime is not installed")
        if any(importlib.util.find_spec(name) is None for name in ("transformers", "torch", "tokenizers", "safetensors")):
            raise ValueError("Tablature Transformers runtime is not installed")
        if not validator_available():
            raise ValueError("Independent notation validator is not installed; npm ci --prefix tools/notation")
        if cfg.tab_model_dir:
            root = Path(cfg.tab_model_dir)
            if (root / "MODEL_REVISION").read_text().strip() != cfg.tab_model_revision:
                raise ValueError("Local MODEL_REVISION differs from configured pinned revision")
            paths = [root / filename for filename in ("model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json")]
        else:
            from huggingface_hub import try_to_load_from_cache
            paths = [try_to_load_from_cache(MODEL_ID, filename, revision=cfg.tab_model_revision)
                     for filename in ("model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json")]
            if not all(isinstance(path, str) for path in paths):
                return {**base, "status": "download_required" if cfg.tab_allow_download else "unavailable",
                        "message": "Pinned weights are not cached. Downloads are " + ("enabled." if cfg.tab_allow_download else "disabled; operator setup required.")}
            paths = list(map(Path, paths))
        for path in paths:
            if not path.is_file() or path.stat().st_size <= 0:
                raise ValueError(f"Missing or empty model file: {path.name}")
            with path.open("rb") as stream:
                stream.read(1)
        if json.loads(paths[1].read_text()).get("model_type") != "t5":
            raise ValueError("Model config is not T5")
        for path in paths[2:]:
            json.loads(path.read_text())
        return {**base, "status": "available", "message": "Pinned model files available; inference not verified by health check."}
    except (ValueError, OSError, ImportError) as exc:
        return {**base, "status": "unavailable", "message": str(exc)}
