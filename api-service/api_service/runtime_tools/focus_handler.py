"""Runtime handler for locating and focusing segmentation labels."""

from __future__ import annotations

import os
from pathlib import PurePosixPath

from api_service.runtime.gui_state import enqueue_gui_command
from backend_common.settings import ROOT_DIR

from .case_resolver import CONTAINER_CASE_ROOT
from .focus_label import get_label_centroid
from .types import ToolTextContent, error_response, text_response
from .viewer_paths import (
    DEFAULT_SEGMENTATION_FILENAME,
    VOLUME_FILE_SUFFIXES,
    current_case_relative_output_path,
    local_output_root,
    looks_like_segmentation,
)


def _requested_segmentation(arguments: dict) -> str | None:
    for key in ("segmentation_file", "segmentation_volume", "segmentation_path", "segmentation_map"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_segmentation(arguments: dict, gui_state: dict) -> tuple[dict | None, str | None]:
    current_case_path = current_case_relative_output_path(gui_state)
    if not current_case_path:
        return None, "Error: No case is loaded. Upload an MRI first."

    case_root = os.path.join(local_output_root(), current_case_path)
    requested_value = _requested_segmentation(arguments)
    normalized_value = str(requested_value or DEFAULT_SEGMENTATION_FILENAME).strip()
    if not normalized_value:
        normalized_value = DEFAULT_SEGMENTATION_FILENAME

    if normalized_value.startswith(f"{CONTAINER_CASE_ROOT}/"):
        relative_path = normalized_value[len(f"{CONTAINER_CASE_ROOT}/") :].strip("/")
        display_path = f"{CONTAINER_CASE_ROOT}/{relative_path}" if relative_path else CONTAINER_CASE_ROOT
    elif normalized_value.startswith(f"{current_case_path}/"):
        relative_path = normalized_value[len(current_case_path) :].strip("/")
        display_path = f"{current_case_path}/{relative_path}" if relative_path else current_case_path
    else:
        path_obj = PurePosixPath(normalized_value)
        if path_obj.is_absolute():
            return None, (
                "Error: segmentation_file must be a filename, a current-case relative path like "
                "`mri/aseg.auto_noCCseg.mgz`, `/case/...`, or a readable "
                "`workspaces/<workspace-name>/cases/<case-name>/...` path."
            )
        relative_path = "/".join(path_obj.parts)
        if not relative_path:
            return None, "Error: segmentation_file must not be empty."
        if "/" not in relative_path:
            relative_path = f"mri/{relative_path}"
        display_path = f"{current_case_path}/{relative_path}"

    disk_path = os.path.join(case_root, *PurePosixPath(relative_path).parts)
    resolved_case_root = os.path.realpath(os.path.abspath(case_root))
    resolved_disk_path = os.path.realpath(os.path.abspath(disk_path))
    if os.path.commonpath([resolved_disk_path, resolved_case_root]) != resolved_case_root:
        return None, "Error: segmentation_file must stay inside the active case output directory."

    return {
        "disk_path": disk_path,
        "display_path": display_path,
        "requested_value": normalized_value,
        "used_default": requested_value is None,
    }, None


def _segmentation_volumes(current_case_path: str) -> list[str]:
    case_root = os.path.join(local_output_root(), current_case_path)
    mri_dir = os.path.join(case_root, "mri")
    if not os.path.isdir(mri_dir):
        return []
    volumes: list[str] = []
    for root, _dirs, filenames in os.walk(mri_dir):
        for filename in sorted(filenames):
            if filename.lower().endswith(VOLUME_FILE_SUFFIXES) and looks_like_segmentation(filename):
                relative = os.path.relpath(os.path.join(root, filename), case_root).replace(os.sep, "/")
                volumes.append(relative)
    return sorted(set(volumes))


def _missing_segmentation_message(segmentation: dict, current_case_path: str) -> str:
    available = _segmentation_volumes(current_case_path)
    if available:
        available_text = "Available segmentation volumes in this case: " + ", ".join(f"`{path}`" for path in available) + "."
    else:
        available_text = "No segmentation volumes were found under `/case/mri/`."

    if segmentation["used_default"]:
        return (
            f"Failed to focus label: default segmentation `{DEFAULT_SEGMENTATION_FILENAME}` "
            f"was not found for the current case (looked for `{segmentation['display_path']}`). "
            f"{available_text} Pass `segmentation_file` to `gui_focus_label` to use a different segmentation map."
        )
    return (
        f"Failed to focus label: segmentation file `{segmentation['requested_value']}` "
        f"was not found in the current case (resolved to `{segmentation['display_path']}`). "
        f"{available_text}"
    )


def handle_gui_focus_label(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Find a segmentation label centroid and request a viewer cursor move."""
    label_query = arguments.get("label_query") or arguments.get("label_name")
    if not label_query:
        return error_response("Missing label_query argument.")

    current_case_path = current_case_relative_output_path(gui_state)
    if not current_case_path:
        return error_response("No case is loaded. Upload an MRI first.")

    segmentation, segmentation_error = _resolve_segmentation(arguments, gui_state)
    if segmentation_error:
        return error_response(segmentation_error)
    if segmentation is None:
        return error_response("Unable to resolve a segmentation file for the active case.")
    if not os.path.isfile(segmentation["disk_path"]):
        return error_response(_missing_segmentation_message(segmentation, current_case_path))

    lut_path = str(ROOT_DIR / "config" / "FreeSurferColorLUT.txt")
    try:
        data = get_label_centroid(segmentation["disk_path"], str(label_query), lut_path)
        if "error" in data:
            return text_response(f"Failed to focus label: {data['error']}")
        x, y, z = data["x"], data["y"], data["z"]
        command_id = enqueue_gui_command(gui_state, "move_cursor", {"position": [x, y, z]})
        return text_response(
            f"Found centroid for {data['label_name']} ({data['label_id']}) and queued "
            f"GUI command {command_id} to move the cursor to ({x}, {y}, {z})."
        )
    except Exception as exc:
        return error_response(f"An unexpected error occurred during label focus: {exc}")
