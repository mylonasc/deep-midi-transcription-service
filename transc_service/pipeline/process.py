"""Killable stage subprocesses: cancellation includes probes, model load and FFmpeg."""
import asyncio
import json
import os
import signal
import sys
from collections import deque
from pathlib import Path


async def run_stage(stage, payload, directory: Path, cancel, progress_cb=None):
    request = directory / f"{stage}-request.json"
    request.write_text(json.dumps(payload))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "transc_service.pipeline.stage_worker", stage, str(request),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    diagnostics = deque(maxlen=12)

    async def read_diagnostics():
        # Progress bars can contain carriage returns without newlines. Drain in
        # bounded chunks, concurrently, so verbose children cannot block on stderr.
        while chunk := await process.stderr.read(4096):
            diagnostics.append(chunk.decode(errors="replace"))

    stderr_reader = asyncio.create_task(read_diagnostics())

    async def read():
        result = None
        tail = []
        async for line in process.stdout:
            text = line.decode(errors="replace").strip()
            if text.startswith("SERVICE_EVENT:"):
                event = json.loads(text.removeprefix("SERVICE_EVENT:"))
                if event["type"] == "progress" and progress_cb:
                    progress_cb(*event["value"])
                elif event["type"] == "result":
                    result = event["value"]
            else:
                tail = (tail + [text])[-12:]
        code = await process.wait()
        await stderr_reader
        if code or result is None:
            reason = "\n".join(("".join(diagnostics) + "\n" + "\n".join(tail)).splitlines()[-12:])
            raise RuntimeError(f"{stage} stage failed (exit {code}): " + reason)
        return result

    reader = asyncio.create_task(read())
    cancelled = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait({reader, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if cancel.is_set():
            raise asyncio.CancelledError
        return await reader
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        cancelled.cancel()
        reader.cancel()
        stderr_reader.cancel()
        await asyncio.gather(cancelled, reader, stderr_reader, return_exceptions=True)
