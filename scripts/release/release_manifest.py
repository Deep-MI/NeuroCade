#!/usr/bin/env python3
"""Create and validate the small manifest used by Apptainer installers."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SAFE_TAG = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")
MANIFEST_ASSET = "neurocade-release.json"


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


def resolve_release(releases_path: Path, channel: str) -> tuple[str, bool]:
    payload = json.loads(releases_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Invalid GitHub releases response")

    compatible: list[tuple[str, bool]] = []
    for release in payload:
        if not isinstance(release, dict) or release.get("draft") is not False:
            continue
        tag = release.get("tag_name")
        prerelease = release.get("prerelease")
        assets = release.get("assets")
        if not isinstance(tag, str) or not SAFE_TAG.fullmatch(tag) or not isinstance(prerelease, bool):
            continue
        if not isinstance(assets, list) or not any(
            isinstance(asset, dict) and asset.get("name") == MANIFEST_ASSET for asset in assets
        ):
            continue
        compatible.append((tag, prerelease))

    if channel == "beta":
        candidates = [release for release in compatible if release[1]]
    elif channel == "stable":
        candidates = [release for release in compatible if not release[1]]
        if not candidates:
            candidates = compatible
    else:
        raise ValueError(f"Unsupported release channel: {channel}")

    if not candidates:
        raise ValueError(f"No compatible NeuroCade {channel} release was found")
    return candidates[0]


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
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("releases", type=Path)
    resolve.add_argument("--channel", choices=("stable", "beta"), required=True)
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
    elif args.command == "read":
        print("\n".join(read_manifest(args.manifest)))
    else:
        tag, prerelease = resolve_release(args.releases, args.channel)
        print(tag)
        print("true" if prerelease else "false")


if __name__ == "__main__":
    main()
