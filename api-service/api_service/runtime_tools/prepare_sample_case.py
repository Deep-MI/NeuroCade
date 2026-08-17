"""Download and verify the bundled NeuroCade sample case."""

from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def install_sample_case(*, url: str, name: str, expected_sha256: str, root: Path) -> Path:
    """Install one verified sample-case archive beneath ``root``."""
    root = root.resolve()
    target = (root / name).resolve()
    if root != target and root not in target.parents:
        raise RuntimeError(f"Unsafe sample case name: {name}")
    if target.is_dir() and any(target.rglob("*")):
        return target

    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
        urllib.request.urlretrieve(url, archive.name)
        with open(archive.name, "rb") as sample_file:
            digest = hashlib.file_digest(sample_file, "sha256").hexdigest()
        if digest != expected_sha256:
            raise RuntimeError(f"Sample archive checksum mismatch: expected {expected_sha256}, got {digest}")
        with tarfile.open(archive.name, "r:gz") as sample_archive:
            for member in sample_archive.getmembers():
                destination = (root / member.name).resolve()
                if root != destination and root not in destination.parents:
                    raise RuntimeError(f"Unsafe sample archive path: {member.name}")
            sample_archive.extractall(root, filter="data")

    if not target.is_dir() or not any(target.rglob("*")):
        raise RuntimeError(f"Sample archive did not create {target}")
    return target


def main() -> None:
    install_sample_case(
        url=os.environ["SAMPLE_CASE_URL"],
        name=os.environ["SAMPLE_CASE_NAME"],
        expected_sha256=os.environ["SAMPLE_CASE_SHA256"],
        root=Path(os.environ.get("SAMPLE_CASE_ROOT", "/sample_case")),
    )


if __name__ == "__main__":
    main()
