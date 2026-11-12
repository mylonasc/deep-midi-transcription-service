"""Private subprocess entry point. Stdout carries prefixed JSON progress/results."""
import asyncio
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path


def emit(kind, value):
    print("SERVICE_EVENT:" + json.dumps({"type": kind, "value": value}),
          file=sys.__stdout__, flush=True)


async def main():
    stage, request = sys.argv[1:]
    payload = json.loads(Path(request).read_text())
    cancel = asyncio.Event()
    if stage == "download":
        from .downloader import _download_audio
        from ..models import Job
        path, info = await _download_audio(
            Job.model_validate(payload["job"]), Path(payload["dest"]), cancel,
            lambda fraction: emit("progress", [fraction]),
        )
        emit("result", {"path": str(path), "info": {
            "title": info.get("title"), "duration": info.get("duration"),
        }})
    elif stage == "transcribe":
        from .transcriber import _transcribe_audio, events_to_jsonable
        from ..models import TranscriptionOptions
        midi, events = await _transcribe_audio(
            payload["audio"], TranscriptionOptions.model_validate(payload["options"]),
            cancel, lambda done, total: emit("progress", [done, total]),
        )
        directory = Path(request).parent
        (directory / "out.mid").write_bytes(midi)
        (directory / "events.json").write_text(json.dumps(events_to_jsonable(events)))
        emit("result", {"midi": str(directory / "out.mid"),
                        "events": str(directory / "events.json")})
    elif stage == "tablature":
        from ..tablature.adapter import generate
        from ..tablature.models import TablatureOptions
        result = generate(Path(payload["midi"]).read_bytes(),
                          TablatureOptions.model_validate(payload["options"]),
                          lambda phase, done, total: emit("progress", [phase, done, total]))
        directory = Path(request).parent
        for name, content in result.items():
            (directory / name).write_bytes(content)
        emit("result", {name: str(directory / name) for name in result})
    else:
        raise ValueError(f"Unknown pipeline stage: {stage}")


if __name__ == "__main__":
    # Reserve stdout exclusively for the protocol, including during imports.
    # Third-party print() calls and progress displays belong in stderr.
    with redirect_stdout(sys.stderr):
        asyncio.run(main())
