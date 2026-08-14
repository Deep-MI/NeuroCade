"""Runtime handler for loading active-case resources into the viewer."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Any

from api_service.runtime.gui_state import enqueue_gui_command

from .case_resolver import CONTAINER_CASE_ROOT
from .output_resources import output_descriptor_path_from_file, output_resource_descriptor
from .types import ToolTextContent, error_response, text_response
from .viewer_paths import current_case_relative_output_path, local_output_root, looks_like_segmentation

ALLOWED_VIEWER_SUFFIXES = (
    ".mgz",
    ".mgh",
    ".nii",
    ".nii.gz",
    ".dcm",
    ".dicom",
    ".lut.txt",
)
SURFACE_ROLES = {"pial", "white", "inflated", "sphere", "smoothwm", "orig"}


def _surface_parts(filename: str) -> tuple[str, str] | None:
    parts = filename.split(".")
    if len(parts) == 2 and parts[0] in {"lh", "rh"} and parts[1] in SURFACE_ROLES:
        return parts[0], parts[1]
    return None


def _is_supported_viewer_path(file_path: str) -> bool:
    return file_path.lower().endswith(ALLOWED_VIEWER_SUFFIXES)


def handle_gui_load_layer(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Queue loading of a typed volume or FreeSurfer surface layer."""
    file_path = str(arguments.get("file_path", "")).strip()
    name = arguments.get("name", "")
    visible = arguments.get("visible")

    if not file_path:
        return error_response("file_path is required.")
    if not file_path.startswith(f"{CONTAINER_CASE_ROOT}/"):
        return error_response("gui_load_layer accepts active-case /case/... paths only.")

    filename = os.path.basename(file_path)
    surface_parts = _surface_parts(filename)
    if not _is_supported_viewer_path(file_path) and surface_parts is None:
        return error_response(f"gui_load_layer supports MRI volumes and FreeSurfer surface meshes, not '{filename}'.")

    current_case_path = current_case_relative_output_path(gui_state)
    if not current_case_path:
        return error_response("/case paths require an active case in the GUI state.")
    relative_path = PurePosixPath(file_path.removeprefix(f"{CONTAINER_CASE_ROOT}/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return error_response("file_path must stay inside the active /case directory.")
    file_path = f"{current_case_path}/{'/'.join(relative_path.parts)}"
    descriptor_path = output_descriptor_path_from_file(file_path)

    output_root = local_output_root()
    disk_path = os.path.join(output_root, file_path)
    if not os.path.isfile(disk_path):
        return error_response(f"file not found on disk: {file_path}. Check the exact filename with case_file_tree.")

    display_name = name or filename.replace(".mgz", "").replace(".nii.gz", "")
    is_segmentation = looks_like_segmentation(filename)
    is_binary_mask = any(keyword in filename.lower() for keyword in ("mask", "brainmask")) or "_bin" in filename.lower()
    lut_type = "binary" if is_binary_mask else ("freesurfer" if is_segmentation else None)
    layer_type = "surface" if surface_parts else ("segmentation" if is_segmentation else "intensity")
    load_command: dict[str, Any] = {
        "resource": output_resource_descriptor(descriptor_path),
        "filename": filename,
        "name": display_name,
        "type": layer_type,
    }
    if lut_type:
        load_command["lut"] = lut_type
    if isinstance(visible, bool):
        load_command["visible"] = visible

    if surface_parts:
        hemisphere, role = surface_parts
        load_command["hemisphere"] = "left" if hemisphere == "lh" else "right"
        load_command["role"] = role
        case_prefix = str(PurePosixPath(file_path).parent.parent)
        companion_paths = (
            ("curvature_resource", f"{case_prefix}/surf/{hemisphere}.curv"),
            ("annotation_resource", f"{case_prefix}/label/{hemisphere}.aparc.DKTatlas.mapped.annot"),
        )
        for resource_key, companion_path in companion_paths:
            companion_disk_path = os.path.join(output_root, companion_path)
            if os.path.isfile(companion_disk_path):
                load_command[resource_key] = output_resource_descriptor(output_descriptor_path_from_file(companion_path))

    command_id = enqueue_gui_command(gui_state, "load_layer", load_command)
    return text_response(f"Queued GUI command {command_id} to load {layer_type} layer '{display_name}' ({descriptor_path}).")
