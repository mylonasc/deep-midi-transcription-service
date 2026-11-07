"""Offline weight availability checks. This does not prove inference readiness."""
import json
from pathlib import Path

from .config import ServerConfig


def _cached_file(size: str, filename: str) -> Path | None:
    from huggingface_hub import try_to_load_from_cache
    cached = try_to_load_from_cache(f"MuScriptor/muscriptor-{size}", filename)
    return Path(cached) if isinstance(cached, str) else None


def _check_files(weights: Path, config: Path):
    with weights.open("rb") as stream:
        if not stream.read(1):
            raise ValueError("Weights file is empty")
    data = json.loads(config.read_text())
    if not isinstance(data, dict) or any(
        type(data.get(key)) is not int or data[key] <= 0
        for key in ("dim", "num_heads", "num_layers", "card")
    ):
        raise ValueError("Model config.json is missing valid architecture fields")


def model_availability(settings: ServerConfig) -> dict:
    models = []
    for size in ("small", "medium", "large"):
        configured = bool(getattr(settings, f"weights_{size}"))
        result = {"size": size, "source": "configured" if configured else "huggingface_cache"}
        try:
            if configured:
                weights = Path(settings.weights_for(size))
                config = weights.with_name("config.json")
            else:
                weights = _cached_file(size, "model.safetensors")
                config = _cached_file(size, "config.json")
            if weights is None or config is None:
                result.update(status="download_required", message="Not fully cached. The first job will download model files.")
            else:
                _check_files(weights, config)
                result.update(status="available", message="Weights and model configuration are present and readable.")
        except (OSError, ValueError, ImportError) as exc:
            result.update(status="unavailable", message=str(exc))
        models.append(result)
    return {"default_model": settings.default_model, "models": models,
            "scope": "Local file availability only; model loading and inference are not tested."}
