"""Persistent host-only authorization and bounded reads of linked sources."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .protocol import PROTOCOL_VERSION, validate_relative_path

_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_SOURCE_SCAN_DEPTH = 32
MAX_SOURCE_READ_BYTES = 8 * 1024 * 1024


class LinkedSourceRegistry:
    """Persistent host-only authorizations for external read-only directories."""

    def __init__(self, path: Path, *, max_files: int = 30_000) -> None:
        if max_files <= 0:
            raise ValueError("Linked source file limit must be positive")
        self.path = path.expanduser().resolve()
        self.max_files = max_files
        self.max_entries = max_files * 4
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            if self.path.stat().st_mode & 0o077:
                raise RuntimeError("Linked source registry permissions must be 0600")
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise RuntimeError("Linked source registry is invalid")
        return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}

    def _save(self, value: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def register(self, value: str | Path) -> dict[str, Any]:
        source = Path(value).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise ValueError("Linked source must be a directory")
        if source == Path(source.anchor):
            raise ValueError("The filesystem root cannot be linked")
        stat = source.stat()
        with self._lock:
            records = self._load()
            source_id = uuid.uuid4().hex
            record = {
                "path": str(source),
                "device": stat.st_dev,
                "inode": stat.st_ino,
                "created_at": int(time.time()),
            }
            records[source_id] = record
            self._save(records)
            return self._public(source_id, record)

    @staticmethod
    def _public(source_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "name": Path(str(record["path"])).name,
            "available": True,
            "created_at": record.get("created_at"),
        }

    def resolve_root(self, source_id: str) -> Path:
        if not _SOURCE_ID.fullmatch(str(source_id or "")):
            raise ValueError("Invalid linked source ID")
        with self._lock:
            record = self._load().get(source_id)
        if record is None:
            raise ValueError("Linked source is not authorized")
        try:
            source = Path(str(record.get("path") or "")).resolve(strict=True)
            stat = source.stat()
        except OSError as exc:
            raise ValueError("Linked source is unavailable or has been replaced") from exc
        if not source.is_dir() or stat.st_dev != record.get("device") or stat.st_ino != record.get("inode"):
            raise ValueError("Linked source is unavailable or has been replaced")
        return source

    def revoke(self, source_id: str) -> bool:
        """Remove one opaque source capability from the private registry."""
        if not _SOURCE_ID.fullmatch(str(source_id or "")):
            raise ValueError("Invalid linked source ID")
        with self._lock:
            records = self._load()
            removed = records.pop(source_id, None) is not None
            if removed:
                self._save(records)
        return removed

    def resolve_path(self, source_id: str, relative_value: str, *, file_only: bool = False) -> Path:
        root = self.resolve_root(source_id)
        relative = validate_relative_path(relative_value, label="Linked source path", allow_dot=True)
        candidate = root
        for part in PurePosixPath(relative).parts:
            if part == ".":
                continue
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValueError("Linked source paths cannot traverse symbolic links")
        candidate = candidate.resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Linked source path escapes its authorized directory") from exc
        if file_only and not candidate.is_file():
            raise ValueError("Linked source path must be a regular file")
        if not file_only and not (candidate.is_file() or candidate.is_dir()):
            raise ValueError("Linked source path must be a regular file or directory")
        return candidate

    def manifest(self, source_id: str) -> dict[str, Any]:
        root = self.resolve_root(source_id)
        entries: list[dict[str, Any]] = []
        file_count = 0
        skipped_symlinks = 0
        truncated = False
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name.casefold(), reverse=True)
            except OSError as exc:
                raise ValueError(f"Linked source cannot be scanned: {exc}") from exc
            for child in children:
                if child.is_symlink():
                    skipped_symlinks += 1
                    continue
                child_path = Path(child.path)
                relative = child_path.relative_to(root).as_posix()
                try:
                    if child.is_dir(follow_symlinks=False):
                        if len(entries) >= self.max_entries:
                            truncated = True
                            stack.clear()
                            break
                        entries.append({"path": relative, "type": "directory"})
                        if depth < MAX_SOURCE_SCAN_DEPTH:
                            stack.append((child_path, depth + 1))
                        else:
                            truncated = True
                            stack.clear()
                            break
                    elif child.is_file(follow_symlinks=False):
                        if file_count >= self.max_files:
                            truncated = True
                            stack.clear()
                            break
                        if len(entries) >= self.max_entries:
                            truncated = True
                            stack.clear()
                            break
                        entries.append({"path": relative, "type": "file", "size": child.stat(follow_symlinks=False).st_size})
                        file_count += 1
                except OSError:
                    continue
        entries.sort(key=lambda item: str(item["path"]).casefold())
        return {
            "protocol_version": PROTOCOL_VERSION,
            "source_id": source_id,
            "name": root.name,
            "entries": entries,
            "truncated": truncated,
            "skipped_symlinks": skipped_symlinks,
        }
