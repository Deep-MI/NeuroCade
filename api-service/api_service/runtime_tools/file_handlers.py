"""Case filesystem and FreeSurfer LUT runtime-tool handlers."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .case_resolver import CONTAINER_CASE_ROOT
from .lut import get_by_id, search_lut
from .types import ToolTextContent, error_response, text_response
from .viewer_paths import local_output_root, resolve_case_mount_local_dir


def handle_case_file_tree(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Return a bounded file tree for the currently active case."""
    case_local_dir = resolve_case_mount_local_dir(gui_state)
    if not case_local_dir:
        return error_response("no active case is selected, so /case is not available.")

    case_root = Path(case_local_dir)
    relative = str(arguments.get("path") or ".").strip().strip("/") or "."
    parts = PurePosixPath(relative).parts
    if ".." in parts:
        return error_response("file-tree path must stay inside the active case.")
    selected_root = (case_root / Path(*parts)).resolve()
    if os.path.commonpath([str(selected_root), str(case_root.resolve())]) != str(case_root.resolve()):
        return error_response("file-tree path must stay inside the active case.")
    if not selected_root.is_dir():
        return error_response(f"directory not found under /case: {relative}")
    max_entries = max(1, min(int(arguments.get("max_entries") or 500), 500))
    selected_case_path = "/case" if relative == "." else f"/case/{relative}"
    lines = [
        "Current case directory is mounted at /case",
        f"Container source: {case_root}",
        "",
        f"File tree for {selected_case_path}/:",
        f"{selected_case_path}/",
    ]
    count = 0
    for root, dirs, files in os.walk(selected_root):
        dirs.sort()
        files.sort()
        rel_root = Path(os.path.relpath(root, selected_root))
        depth = 0 if str(rel_root) == "." else len(rel_root.parts)
        for directory in dirs:
            rel_path = Path(directory) if str(rel_root) == "." else rel_root / directory
            lines.append(f"{'  ' * (depth + 1)}{rel_path.as_posix()}/")
            count += 1
            if count >= max_entries:
                lines.append(f"[truncated after {max_entries} entries; inspect a narrower path]")
                return text_response("\n".join(lines))
        for filename in files:
            rel_path = Path(filename) if str(rel_root) == "." else rel_root / filename
            lines.append(f"{'  ' * (depth + 1)}{rel_path.as_posix()}")
            count += 1
            if count >= max_entries:
                lines.append(f"[truncated after {max_entries} entries; inspect a narrower path]")
                return text_response("\n".join(lines))

    return text_response("\n".join(lines))


def _resolve_lut_volume_path(volume_path: str, gui_state: dict | None) -> str:
    """Resolve an allowed viewer volume path to a local file for LUT filtering."""
    raw = str(volume_path or "").strip()
    if not raw:
        raise ValueError("volume_path is required.")

    if raw == CONTAINER_CASE_ROOT or raw.startswith(f"{CONTAINER_CASE_ROOT}/"):
        case_local_dir = resolve_case_mount_local_dir(gui_state)
        if not case_local_dir:
            raise ValueError("/case paths require an active case in the GUI state.")
        relative_path = raw[len(CONTAINER_CASE_ROOT) :].lstrip("/")
        candidate = os.path.join(case_local_dir, *PurePosixPath(relative_path).parts)
        root = os.path.realpath(case_local_dir)
    elif raw.startswith("/"):
        raise ValueError("Only /case and output-root-relative volume paths are allowed.")
    else:
        output_root = local_output_root()
        candidate = os.path.join(output_root, *PurePosixPath(raw).parts)
        root = os.path.realpath(output_root)

    resolved = os.path.realpath(candidate)
    if os.path.commonpath([resolved, root]) != root:
        raise ValueError("volume_path escapes the allowed data root.")
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"volume_path not found on disk: {raw}")
    return resolved


def _label_ids_in_volume(volume_path: str) -> set[int]:
    """Read unique integer label IDs from a neuroimaging volume."""
    try:
        import nibabel as nib
        import numpy as np
    except Exception as exc:
        raise RuntimeError(f"nibabel and numpy are required to inspect volume labels: {exc}") from exc

    nib_module = cast(Any, nib)
    image = nib_module.load(volume_path)
    values = np.unique(np.asanyarray(image.dataobj))
    labels: set[int] = set()
    for value in values:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(numeric_value) and numeric_value.is_integer():
            labels.add(int(numeric_value))
    return labels


def handle_lut_lookup(arguments: dict, gui_state: dict | None = None) -> list[ToolTextContent]:
    """Look up FreeSurfer label IDs by name or resolve a label name by ID."""
    query = str(arguments.get("query", "")).strip()
    if not query:
        return text_response("No query provided.")

    volume_path = str(arguments.get("volume_path") or arguments.get("volume") or "").strip()
    available_label_ids: set[int] | None = None
    volume_context = ""
    if volume_path:
        try:
            resolved_volume_path = _resolve_lut_volume_path(volume_path, gui_state)
            available_label_ids = _label_ids_in_volume(resolved_volume_path)
            volume_context = f"Filtered to {len(available_label_ids)} unique integer label ID(s) present in {volume_path}."
        except Exception as exc:
            return error_response(f"inspecting volume_path '{volume_path}': {exc}")

    results: list[str] = []
    if query.isdigit():
        label_id = int(query)
        name = get_by_id(label_id) if available_label_ids is None or label_id in available_label_ids else None
        if name and available_label_ids is not None:
            results.append(f"{label_id}\t{name}\t# present in volume")
        elif name:
            results.append(f"{label_id}\t{name}")
        elif available_label_ids is not None:
            results.append(f"No label found for ID {label_id} among labels present in {volume_path}.")
        else:
            results.append(f"No label found for ID {label_id}.")
    else:
        matches, total = search_lut(query, limit=50, allowed_label_ids=available_label_ids)
        if matches:
            results = [f"{match.label_id}\t{match.name}" + (f"\t# {match.comment}" if match.comment else "") for match in matches]
            if total > 50:
                results.append(f"... ({total - 50} more results truncated)")
        elif available_label_ids is not None:
            results.append(f"No labels present in {volume_path} matched '{query}'.")
        else:
            results.append(f"No labels found matching '{query}'.")

    header = "ID\tLabel Name\tMatch Context"
    if volume_context:
        results.insert(0, f"# {volume_context}")
    return text_response(header + "\n" + "\n".join(results))
