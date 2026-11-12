"""Pinned Open-Fret inference in a supervised subprocess, never the API process."""
import json
from pathlib import Path
import re
import tempfile
import time
import subprocess

from ..config import ServerConfig
from .export import export_result
from .midi import inspect_midi
from .models import Fingering, ModelProvenance, TablatureOptions, TablatureResult

SOURCE_REVISION = "3a62b4cb06ff2900dc1d494b98fb5a3acb174f9f"
MODEL_ID = "mylonasc/open-fret-tab"


def preflight(data: bytes, options: TablatureOptions, cfg: ServerConfig):
    inspected = inspect_midi(data)
    summary = inspected.inspection
    if summary.source_midi_sha256 != options.source_midi_sha256:
        raise ValueError("Source MIDI changed; inspect it again before generating tablature")
    part = next((part for part in summary.parts if part.id == options.part_id), None)
    if part is None:
        raise ValueError("Selected MIDI part does not exist")
    blockers = [item.message for item in summary.diagnostics + part.diagnostics if item.blocks_generation]
    if blockers:
        raise ValueError(" ".join(blockers))
    if part.note_count > cfg.tab_max_notes:
        raise ValueError(f"Selected part exceeds the {cfg.tab_max_notes}-note limit")
    return inspected


def generate(data: bytes, options: TablatureOptions, progress=lambda *args: None):
    cfg = ServerConfig()
    inspected = preflight(data, options, cfg)
    started = time.monotonic()
    progress("loading_model", 0, 1)
    import open_fret
    if Path(open_fret.__file__).with_name("SOURCE_REVISION").read_text().strip() != SOURCE_REVISION:
        raise ValueError("Open-Fret source revision mismatch; install the pinned inference package")
    import torch
    from transformers import AutoTokenizer, T5ForConditionalGeneration
    from open_fret.generation import chunk_complete_onsets, generate_tab
    from open_fret.events import notes_to_tokens
    from open_fret.midi_io import add_conditioning
    from open_fret.postprocess import NeighborSearchCorrector
    from dataclasses import asdict

    if cfg.tab_device not in {"cpu", "cuda"}:
        raise ValueError("OPEN_FRET_DEVICE must be cpu or cuda")
    if not re.fullmatch(r"[0-9a-f]{40}", cfg.tab_model_revision):
        raise ValueError("OPEN_FRET_MODEL_REVISION must be a pinned 40-character commit")
    source = cfg.tab_model_dir or MODEL_ID
    kwargs = {"local_files_only": not cfg.tab_allow_download, "trust_remote_code": False}
    if not cfg.tab_model_dir:
        kwargs["revision"] = cfg.tab_model_revision
    else:
        # Local operator-provided weights must carry the same pinned provenance.
        if (Path(source) / "MODEL_REVISION").read_text().strip() != cfg.tab_model_revision:
            raise ValueError("Local model MODEL_REVISION does not match configured revision")
    tokenizer = AutoTokenizer.from_pretrained(source, **kwargs)
    model = T5ForConditionalGeneration.from_pretrained(source, **kwargs).to(cfg.tab_device)
    model.eval()
    source_notes = inspected.notes_by_part[options.part_id]
    notes = [{"id": n.id, "pitch": n.pitch, "start": n.start_tick, "end": n.end_tick} for n in source_notes]
    tokens = notes_to_tokens(notes)
    chunks = list(chunk_complete_onsets(tokens, 509))
    raw, windows = [], []
    with torch.inference_mode():
        for i, chunk in enumerate(chunks):
            progress("inference", i, len(chunks))
            decoded = generate_tab(model, tokenizer, add_conditioning(chunk), mode="anchored", beams=4,
                                   max_input_tokens=512, max_new_tokens=512, stochastic=False)
            raw.extend(decoded.tokens)
            windows.append(decoded.metadata)
    progress("correction", 0, 1)
    _, correction = NeighborSearchCorrector().correct(raw, add_conditioning(tokens), source_notes=notes)
    result = TablatureResult(options=options,
        provenance=ModelProvenance(source_revision=SOURCE_REVISION, model_id=MODEL_ID,
                                   model_revision=cfg.tab_model_revision),
        source_ppq=inspected.inspection.source_ppq, tempo_map=inspected.inspection.tempo_map,
        meter_map=inspected.inspection.meter_map, source_notes=source_notes,
        ignored_part_ids=[part.id for part in inspected.inspection.parts if part.id != options.part_id],
        ignored_drum_attacks=inspected.inspection.ignored_drum_attacks,
        rounded_tick_values=inspected.inspection.rounded_tick_values,
        assignments=[Fingering(source_note_id=note["id"], string=note["string"], fret=note["fret"])
                     for note in correction.corrected_notes],
        correction_counts={name: getattr(correction, name) for name in ("refingered", "inserted", "dropped_extra")},
        limitations=["Fingering feasibility is not a biomechanical playability guarantee.",
                     "Original timing is retained; model/notation ticks use reported 480-PPQ rounding.",
                     "ASCII is an onset preview, not duration notation."])
    progress("export", 0, 1)
    artifacts = export_result(result)
    progress("validating_notation", 0, 1)
    validator = Path(__file__).resolve().parents[2] / "tools" / "notation" / "verify.cjs"
    verification = subprocess.run(["node", str(validator)],
        input=json.dumps({"document": json.loads(artifacts["tablature.json"]), "tex": artifacts["score.tex"].decode()}),
        text=True, capture_output=True, timeout=60)
    if verification.returncode:
        raise ValueError("Independent AlphaTex import failed: " + verification.stderr[-3000:])
    artifacts["report.json"] = json.dumps({"source_midi_sha256": options.source_midi_sha256,
        "provenance": result.provenance.model_dump(), "windows": windows, "raw_tokens": raw,
        "correction": asdict(correction), "elapsed_seconds": time.monotonic() - started}).encode()
    progress("complete", len(chunks), len(chunks))
    return artifacts


async def run_tablature(data, options, cancel, progress=None):
    import asyncio
    from ..pipeline.process import run_stage
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "input.mid").write_bytes(data)
        async with asyncio.timeout(ServerConfig().tab_timeout_s):
            result = await run_stage("tablature", {"midi": str(root / "input.mid"),
                "options": options.model_dump(mode="json")}, root, cancel, progress)
        return {name: Path(path).read_bytes() for name, path in result.items()}
