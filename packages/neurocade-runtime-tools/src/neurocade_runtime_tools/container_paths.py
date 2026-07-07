"""Filesystem paths used by NeuroCade runtime helpers."""

from __future__ import annotations

import os
from pathlib import Path


FREESURFER_LICENSE_CONTAINER_PATH = "/fs_license.txt"


def find_repo_root(start: Path | None = None) -> Path:
    """Find the NeuroCade repository root from a starting path."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "scripts").is_dir() and (candidate / "packages" / "neurocade-runtime-tools").is_dir():
            return candidate
    return current


def license_path(root: Path | None = None, data_root: Path | str | None = None) -> Path | None:
    """Return the first configured or repository FreeSurfer license path."""
    configured = os.environ.get("FREESURFER_LICENSE")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if data_root:
        candidates.append(Path(data_root).expanduser() / "license.txt")
    repo_root = find_repo_root(root)
    candidates.extend(
        [
            repo_root / "neurocade-data" / "license.txt",
            repo_root / "license.txt",
        ]
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate.resolve()
        except OSError:
            continue
    return None
