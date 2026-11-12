"""Install only the pinned MIT inference sources, never weights or corpus data.

Usage: python tools/install_open_fret.py /path/to/environment/site-packages
The archive is content-verified before any files are written.
"""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import urllib.request

REVISION = "3a62b4cb06ff2900dc1d494b98fb5a3acb174f9f"
ARCHIVE_SHA256 = "ca6efaa050ebcff8d2a6e4242ef667d55c757d550598e93276a4a096a6194437"
MODULES = ("inference", "events", "generation", "midi_io", "postprocess",
           "transcription_diagnostics", "token_metadata", "render", "alphatex")


def install(destination):
    data = urllib.request.urlopen(
        f"https://codeload.github.com/mylonasc/open-fret/tar.gz/{REVISION}", timeout=120).read()
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("Open-Fret archive checksum mismatch")
    target = Path(destination) / "open_fret"
    if target.exists():
        raise RuntimeError(f"Refusing to overwrite existing package: {target}")
    # Read only named regular files; never extract paths/symlinks from a tarball.
    prefix = f"open-fret-{REVISION}/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        contents = {}
        for name in MODULES:
            contents[name + ".py"] = archive.extractfile(prefix + f"open_fret/{name}.py").read()
        contents["LICENSE"] = archive.extractfile(prefix + "LICENSE").read()
    target.mkdir(parents=True)
    for name, content in contents.items():
        (target / name).write_bytes(content)
    (target / "__init__.py").write_text('"""Pinned inference-only Open-Fret (MIT)."""\n')
    (target / "SOURCE_REVISION").write_text(REVISION + "\n")
    print(f"Installed Open-Fret inference sources {REVISION} into {target}")


if __name__ == "__main__":
    install(sys.argv[1])
