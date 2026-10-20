"""Case-scoped access to managed and linked files."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import HTTPException
from neurocade_runtime_tools.bridge_client import BridgeClient, BridgeError
from sqlalchemy.orm import Session

from api_service.helpers import get_case_for_user
from api_service.policies import require_case_read
from api_service.schemas import CaseFileSummary
from backend_common.auth import AuthContext
from backend_common.db import Case, LinkedCaseSource

CaseFileSource = Literal["managed", "linked"]
CaseFileKind = Literal["file", "directory"]
CHUNK_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True)
class ResolvedCaseFile:
    source: CaseFileSource
    relative_path: str
    name: str
    kind: CaseFileKind
    size: int | None
    managed_path: Path | None = None
    bridge: BridgeClient | None = None
    bridge_source_id: str | None = None
    bridge_source_relative: str = "."
    bridge_path: str | None = None

    def chunks(self, *, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        if self.kind != "file":
            raise ValueError("Directories cannot be read")
        if self.managed_path is not None:
            with self.managed_path.open("rb") as stream:
                stream.seek(start)
                remaining = None if end is None else end - start + 1
                while remaining is None or remaining > 0:
                    chunk = stream.read(CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining))
                    if not chunk:
                        return
                    yield chunk
                    if remaining is not None:
                        remaining -= len(chunk)
            return
        assert self.bridge is not None and self.bridge_source_id is not None and self.bridge_path is not None
        offset = start
        expected = self.size
        while end is None or offset <= end:
            length = CHUNK_SIZE if end is None else min(CHUNK_SIZE, end - offset + 1)
            content, current_size = self.bridge.read_linked_source(
                self.bridge_source_id, self.bridge_path, offset=offset, length=length
            )
            expected = current_size if expected is None else expected
            if current_size != expected:
                raise RuntimeError("Linked source changed while it was being read")
            if not content:
                if offset < current_size:
                    raise RuntimeError("Linked source ended before its recorded size")
                return
            yield content
            offset += len(content)
            if offset >= current_size:
                return


def safe_case_path(value: str, *, allow_dot: bool = False) -> PurePosixPath:
    if (
        not value
        or "\\" in value
        or len(value) > 1024
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise HTTPException(status_code=400, detail="Case file path is invalid")
    if value == "." and allow_dot:
        return PurePosixPath(value)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or len(part) > 255 for part in value.split("/")):
        raise HTTPException(status_code=400, detail="Case file path is invalid")
    return path


def linked_case_source(db: Session, case_id: str) -> LinkedCaseSource:
    source = db.query(LinkedCaseSource).filter(LinkedCaseSource.case_id == case_id).one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Case does not have a linked source")
    return source


def linked_source_path(source: LinkedCaseSource, relative: PurePosixPath) -> str:
    return relative.as_posix() if source.source_relative == "." else (
        PurePosixPath(source.source_relative) / relative
    ).as_posix()


def _managed_path(case_dir: Path, relative: PurePosixPath) -> Path:
    root = case_dir.resolve(strict=True)
    candidate = root
    for part in relative.parts:
        if part == ".":
            continue
        candidate /= part
        if candidate.is_symlink():
            raise HTTPException(status_code=404, detail="Case file not found")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Case file not found") from exc
    return resolved


def linked_case_manifest(source: LinkedCaseSource) -> tuple[BridgeClient, list[tuple[dict, str]]]:
    client = BridgeClient.from_environment()
    try:
        manifest = client.linked_source_manifest(source.source_id)
    except (BridgeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"Linked source unavailable: {exc}") from exc
    entries = manifest.get("entries")
    if not isinstance(entries, list) or manifest.get("truncated"):
        raise HTTPException(status_code=409, detail="Linked source manifest is incomplete")
    root = PurePosixPath(source.source_relative)
    scoped: list[tuple[dict, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        full = safe_case_path(entry["path"])
        try:
            relative = full if source.source_relative == "." else full.relative_to(root)
        except ValueError:
            continue
        if relative.as_posix() != ".":
            scoped.append((entry, relative.as_posix()))
    return client, scoped


def linked_case_file(
    source: LinkedCaseSource, relative_path: str, client: BridgeClient, size: int | None = None
) -> ResolvedCaseFile:
    relative = safe_case_path(relative_path)
    return ResolvedCaseFile(
        "linked", relative.as_posix(), relative.name, "file", size,
        bridge=client, bridge_source_id=source.source_id,
        bridge_source_relative=source.source_relative,
        bridge_path=linked_source_path(source, relative),
    )


def resolve_case_entry(
    db: Session,
    case: Case,
    case_dir: Path,
    source_kind: CaseFileSource,
    relative_path: str,
    *,
    expected_kind: CaseFileKind = "file",
) -> ResolvedCaseFile:
    relative = safe_case_path(relative_path, allow_dot=expected_kind == "directory")
    if source_kind == "managed":
        path = _managed_path(case_dir, relative)
        actual_kind: CaseFileKind = "directory" if path.is_dir() else "file" if path.is_file() else expected_kind
        if actual_kind != expected_kind:
            raise HTTPException(status_code=400, detail=f"Case input must be an existing {expected_kind}")
        return ResolvedCaseFile(
            "managed", relative.as_posix(), path.name, actual_kind,
            path.stat().st_size if path.is_file() else None, managed_path=path,
        )

    source = linked_case_source(db, case.id)
    client, entries = linked_case_manifest(source)
    if relative.as_posix() == ".":
        entry_type, size = "directory", None
    else:
        entry = next((item for item, path in entries if path == relative.as_posix()), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="Case file not found")
        entry_type = entry.get("type")
        size = max(0, int(entry.get("size") or 0)) if entry_type == "file" else None
    if entry_type != expected_kind:
        raise HTTPException(status_code=400, detail=f"Case input must be an existing {expected_kind}")
    return ResolvedCaseFile(
        "linked", relative.as_posix(), source.display_name if relative.as_posix() == "." else relative.name,
        expected_kind, size, bridge=client, bridge_source_id=source.source_id,
        bridge_source_relative=source.source_relative,
        bridge_path=linked_source_path(source, relative),
    )


def list_case_files(db: Session, context: AuthContext, case_id: str) -> list[CaseFileSummary]:
    case, _workspace, role, case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    files: list[CaseFileSummary] = []
    if case_dir.exists():
        root = case_dir.resolve(strict=True)
        files.append(CaseFileSummary(path=".", name=case.title, type="directory", source="managed"))
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirnames[:] = sorted(name for name in dirnames if not (parent / name).is_symlink())
            files.extend(
                CaseFileSummary(path=(parent / name).relative_to(root).as_posix(), name=name, type="directory", source="managed")
                for name in dirnames
            )
            files.extend(
                CaseFileSummary(path=(parent / name).relative_to(root).as_posix(), name=name, type="file", size_bytes=(parent / name).stat().st_size, source="managed")
                for name in sorted(filenames) if not (parent / name).is_symlink() and (parent / name).is_file()
            )
    linked = db.query(LinkedCaseSource).filter(LinkedCaseSource.case_id == case.id).one_or_none()
    if linked is not None:
        files.append(CaseFileSummary(path=".", name=linked.display_name, type="directory", source="linked"))
        _client, entries = linked_case_manifest(linked)
        files.extend(
            CaseFileSummary(path=path, name=PurePosixPath(path).name, type=entry["type"], size_bytes=max(0, int(entry.get("size") or 0)) if entry["type"] == "file" else None, source="linked")
            for entry, path in entries if entry.get("type") in {"file", "directory"}
        )
    return sorted(files, key=lambda item: (item.source, item.path.casefold()))


def resolve_case_file(
    db: Session, context: AuthContext, case_id: str, source_kind: CaseFileSource, relative_path: str
) -> ResolvedCaseFile:
    case, _workspace, role, case_dir = get_case_for_user(db, case_id, context.user.id)
    require_case_read(role)
    return resolve_case_entry(db, case, case_dir, source_kind, relative_path)
