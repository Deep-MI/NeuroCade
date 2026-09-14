"""Build a desktop bundle with a one-time grant, never a permanent credential."""

import io
import json
import os
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException


def host_executable() -> str:
    executable = os.environ.get("NEUROCADE_MCP_HOST_EXECUTABLE", "")
    if not PurePosixPath(executable).is_absolute():
        raise HTTPException(409, "Start NeuroCade with local agents enabled to install the host connector first.")
    return executable


def desktop_bundle(pairing: dict) -> bytes:
    executable = host_executable()
    resources = Path(__file__).with_name("desktop_extension")
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "launcher.cjs", "logo.png"):
            archive.writestr(name, (resources / name).read_bytes())
        archive.writestr("installation.json", json.dumps({
            "executable": executable,
            **pairing,
        }))
    return buffer.getvalue()
