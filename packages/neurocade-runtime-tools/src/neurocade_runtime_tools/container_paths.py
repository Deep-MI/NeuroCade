"""Filesystem paths used by NeuroCade runtime catalog helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


DEFAULT_CATALOG_DIR = Path("llm-data/tool-catalog")
DEFAULT_INVENTORY_JSON = DEFAULT_CATALOG_DIR / "installed_containers.json"
DEFAULT_INSTALLED_TOOLS_JSONL = DEFAULT_CATALOG_DIR / "installed_tools.jsonl"
HELP_CACHE_JSONL = DEFAULT_CATALOG_DIR / "help_cache.jsonl"
IGNORED_COMMANDS_JSONL = DEFAULT_CATALOG_DIR / "ignored_commands.jsonl"

CONTAINER_PROBE_CWD = Path("/tmp/neurocade_tmp")
DISCOVERY_COMMANDS_FILE = "commands.txt"
CONTAINER_TOOLS_JSONL = "tool_index.jsonl"
CONTAINER_IGNORED_COMMANDS_JSONL = "ignored_commands.jsonl"
CONTAINER_INDEX_META_JSON = "index_meta.json"
CONTAINER_INDEX_SCHEMA_VERSION = 1
FREESURFER_LICENSE_CONTAINER_PATH = "/fs_license.txt"


def find_repo_root(start: Path | None = None) -> Path:
    """Find the NeuroCade repository root from a starting path."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "scripts").is_dir() and (candidate / "packages" / "neurocade-runtime-tools").is_dir():
            return candidate
    return current


def catalog_dir(root: Path | None = None) -> Path:
    """Return the directory that stores generated tool catalog files."""
    configured = os.environ.get("TOOL_CATALOG_DIR")
    return Path(configured).expanduser().resolve() if configured else (find_repo_root(root) / DEFAULT_CATALOG_DIR).resolve()


def inventory_path(root: Path | None = None) -> Path:
    """Return the generated installed-container inventory path."""
    configured = os.environ.get("NEUROCADE_CONTAINER_INVENTORY")
    return Path(configured).expanduser().resolve() if configured else catalog_dir(root) / DEFAULT_INVENTORY_JSON.name


def installed_tools_path(root: Path | None = None) -> Path:
    """Return the generated installed-tool index path."""
    configured = os.environ.get("NEUROCADE_INSTALLED_TOOLS_JSONL")
    return Path(configured).expanduser().resolve() if configured else catalog_dir(root) / DEFAULT_INSTALLED_TOOLS_JSONL.name


def help_cache_path(root: Path | None = None) -> Path:
    """Return the JSONL cache path for mined command help text."""
    return catalog_dir(root) / HELP_CACHE_JSONL.name


def ignored_commands_path(root: Path | None = None) -> Path:
    """Return the JSONL sidecar path for commands skipped during help mining."""
    return catalog_dir(root) / IGNORED_COMMANDS_JSONL.name


def index_lock_path(root: Path | None = None) -> Path:
    """Return the lock path that serializes catalog index writes."""
    return catalog_dir(root) / "index.lock"


def container_tool_index_path(container: dict[str, Any]) -> Path:
    """Return the per-container installed-tool sidecar path."""
    return Path(str(container["image_path"])).parent / CONTAINER_TOOLS_JSONL


def container_ignored_commands_path(container: dict[str, Any]) -> Path:
    """Return the per-container ignored-command sidecar path."""
    return Path(str(container["image_path"])).parent / CONTAINER_IGNORED_COMMANDS_JSONL


def container_index_meta_path(container: dict[str, Any]) -> Path:
    """Return the per-container index metadata sidecar path."""
    return Path(str(container["image_path"])).parent / CONTAINER_INDEX_META_JSON


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
