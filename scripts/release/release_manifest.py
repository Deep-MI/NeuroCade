#!/usr/bin/env python3
"""Create and validate the small manifest used by Apptainer installers."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_TAG = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")


def _safe(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label} in release manifest")
    return value


def read_manifest(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("architecture") != "amd64":
        raise ValueError("Unsupported NeuroCade release manifest")
    tag = _safe(payload.get("tag"), SAFE_TAG, "tag")
    version = _safe(payload.get("version"), SAFE_NAME, "version")
    if tag != f"v{version}":
        raise ValueError("Release tag and version do not match")

    values = [tag, version]
    for artifact_name in ("application_sif", "runtime_bridge"):
        artifact = payload.get(artifact_name)
        if not isinstance(artifact, dict):
            raise ValueError(f"Missing {artifact_name} in release manifest")
        filename = _safe(artifact.get("filename"), SAFE_NAME, f"{artifact_name} filename")
        checksum = _safe(artifact.get("sha256_filename"), SAFE_NAME, f"{artifact_name} checksum filename")
        if checksum != f"{filename}.sha256":
            raise ValueError(f"Unexpected checksum filename for {artifact_name}")
        values.extend((filename, checksum))
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--tag", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--sif", required=True)
    create.add_argument("--bridge", required=True)
    create.add_argument("--output", type=Path, required=True)

    read = subparsers.add_parser("read")
    read.add_argument("manifest", type=Path)
    args = parser.parse_args()

    if args.command == "create":
        payload = {
            "schema_version": 1,
            "tag": args.tag,
            "version": args.version,
            "architecture": "amd64",
            "application_sif": {"filename": args.sif, "sha256_filename": f"{args.sif}.sha256"},
            "runtime_bridge": {"filename": args.bridge, "sha256_filename": f"{args.bridge}.sha256"},
        }
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        read_manifest(args.output)
    else:
        print("\n".join(read_manifest(args.manifest)))


if __name__ == "__main__":
    main()
