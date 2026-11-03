#!/usr/bin/env python3
"""Hash package installation inputs, excluding generated files and bytecode."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def package_fingerprint(package: Path, identity: str) -> str:
    digest = hashlib.sha256(identity.encode())
    files = [package] if package.is_file() else sorted(
        path for path in package.rglob("*") if path.is_file()
        and not any(part.startswith(".") or part in {"__pycache__", "build", "dist"}
                    or part.endswith(".egg-info") for part in path.relative_to(package).parts)
    )
    if not files:
        raise ValueError(f"Package has no installation inputs: {package}")
    for path in files:
        digest.update(str(path.relative_to(package) if package.is_dir() else path.name).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    print(package_fingerprint(Path(sys.argv[1]), sys.argv[2]))
