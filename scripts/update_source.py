#!/usr/bin/env python3
"""Safely stage and transactionally replace an archive installation's source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

EXCLUDED_TOP_LEVEL = {".env", ".git", ".runtime", ".venv", "neurocade-data", "sample_case"}
EXCLUDED_NAMES = {
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist", "htmlcov", "node_modules"
}


def included(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0] not in EXCLUDED_TOP_LEVEL and not any(
        part in EXCLUDED_NAMES or part.endswith((".egg-info", ".pyc")) for part in relative.parts
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build_manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if included(relative) and path.is_file() and not path.is_symlink():
            result[relative.as_posix()] = digest(path)
    return result


def write_manifest(root: Path, output: Path) -> None:
    output.write_text(json.dumps({"schema_version": 1, "files": build_manifest(root)}, indent=2) + "\n")


def read_manifest(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text())
    files = payload.get("files")
    if payload.get("schema_version") != 1 or not isinstance(files, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in files.items()
    ):
        raise ValueError("Invalid installed-source manifest")
    return files


def check(root: Path, manifest_path: Path) -> None:
    expected_files = read_manifest(manifest_path)
    current_files = build_manifest(root)
    changed = sorted(name for name in set(expected_files) | set(current_files) if expected_files.get(name) != current_files.get(name))
    if changed:
        print("Locally modified managed files:\n" + "\n".join(changed[:20]))
        raise SystemExit(3)


def extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        roots: set[str] = set()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not name.parts:
                raise ValueError("Unsafe path in source archive")
            roots.add(name.parts[0])
            if member.isdev() or member.isfifo() or member.islnk() or member.issym():
                raise ValueError("Unsafe member in source archive")
        if len(roots) != 1:
            raise ValueError("Source archive must contain one top-level directory")
        bundle.extractall(destination)
    root = destination / roots.pop()
    if not (root / "scripts/install.sh").is_file() or not (root / "scripts/update.sh").is_file():
        raise ValueError("Source archive is not a NeuroCade release")
    return root


def apply(source: Path, install: Path, backup: Path, record: Path) -> None:
    previous_manifest_path = install / ".runtime/source-manifest.json"
    previous = read_manifest(previous_manifest_path) if previous_manifest_path.exists() else build_manifest(install)
    new = build_manifest(source)
    backup.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name in sorted(set(new) - set(previous)):
        if not (install / name).exists() and not (install / name).is_symlink():
            created.append(name)
    record.write_text(json.dumps({"created": created, "previous": sorted(previous)}) + "\n")
    for name in sorted(set(previous) | set(new)):
        target = install / name
        if target.exists() or target.is_symlink():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                saved.symlink_to(os.readlink(target))
            else:
                shutil.copy2(target, saved)
        if name in new:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    write_manifest(install, previous_manifest_path)


def rollback(install: Path, backup: Path, record: Path) -> None:
    payload = json.loads(record.read_text())
    for name in payload["created"]:
        target = install / name
        if target.exists() or target.is_symlink():
            target.unlink()
    for name in payload["previous"]:
        saved, target = backup / name, install / name
        if saved.exists() or saved.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            if saved.is_symlink():
                target.symlink_to(os.readlink(saved))
            else:
                shutil.copy2(saved, target)
    write_manifest(install, install / ".runtime/source-manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("write-manifest", "check"):
        command = commands.add_parser(name)
        command.add_argument("root", type=Path)
        command.add_argument("manifest", type=Path)
    command = commands.add_parser("extract")
    command.add_argument("archive", type=Path)
    command.add_argument("destination", type=Path)
    command = commands.add_parser("apply")
    for name in ("source", "install", "backup", "record"):
        command.add_argument(name, type=Path)
    command = commands.add_parser("rollback")
    for name in ("install", "backup", "record"):
        command.add_argument(name, type=Path)
    args = parser.parse_args()
    if args.command == "write-manifest":
        write_manifest(args.root, args.manifest)
    elif args.command == "check":
        check(args.root, args.manifest)
    elif args.command == "extract":
        print(extract(args.archive, args.destination))
    elif args.command == "apply":
        apply(args.source, args.install, args.backup, args.record)
    else:
        rollback(args.install, args.backup, args.record)


if __name__ == "__main__":
    main()
