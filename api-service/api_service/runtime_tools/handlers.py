"""Provide API service handlers behavior for NeuroCade."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any, cast

from dotenv import load_dotenv

from api_service.runtime.gui_state import enqueue_gui_command
from backend_common.settings import ROOT_DIR, get_settings

from .case_resolver import (
    CONTAINER_CASE_ROOT,
)
from .container_commands import (
    _VOLUME_FILE_SUFFIXES,
    FOCUS_LABEL_DEFAULT_SEGMENTATION,
    LOCAL_OUTPUT_ROOT,
    _current_case_relative_output_path,
    _looks_like_segmentation,
    _resolve_case_mount_local_dir,
)
from .lut import get_by_id, search_lut
from .output_resources import (
    output_descriptor_path_from_file,
    output_resource_descriptor,
)
from .types import ToolTextContent, error_response, text_response

load_dotenv(ROOT_DIR / ".env")
settings = get_settings()

logger = logging.getLogger(__name__)
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


def _layers(gui_state: dict) -> list[dict[str, Any]]:
    """Return the typed frontend layer snapshot."""
    values = gui_state.get("layers") or []
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _layer_id(layer: dict[str, Any]) -> str:
    return str(layer.get("id") or layer.get("filename") or "")


def _find_layer(gui_state: dict, requested_id: str) -> dict[str, Any] | None:
    return next(
        (
            layer
            for layer in _layers(gui_state)
            if requested_id in {_layer_id(layer), str(layer.get("filename") or "")}
        ),
        None,
    )


def _missing_layer_error(action: str) -> list[ToolTextContent]:
    return error_response(
        f"{action} requires a loaded layer. Inspect the current state with gui_list_layers "
        "or load one with gui_load_layer."
    )


def handle_case_file_tree(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Return the complete file tree for the currently active case as mounted at /case."""
    case_local_dir = _resolve_case_mount_local_dir(gui_state)
    if not case_local_dir:
        return error_response("no active case is selected, so /case is not available.")

    case_root = Path(case_local_dir)
    lines = [
        "Current case directory is mounted at /case",
        f"Container source: {case_root}",
        "",
        "Complete file tree:",
        "/case/",
    ]
    for root, dirs, files in os.walk(case_root):
        dirs.sort()
        files.sort()
        rel_root = Path(os.path.relpath(root, case_root))
        depth = 0 if str(rel_root) == "." else len(rel_root.parts)
        for directory in dirs:
            rel_path = Path(directory) if str(rel_root) == "." else rel_root / directory
            lines.append(f"{'  ' * (depth + 1)}{rel_path.as_posix()}/")
        for filename in files:
            rel_path = Path(filename) if str(rel_root) == "." else rel_root / filename
            lines.append(f"{'  ' * (depth + 1)}{rel_path.as_posix()}")

    return text_response("\n".join(lines))


# ---------------------------------------------------------------------------
# FreeSurfer LUT lookup runs directly in the api-service process.
# ---------------------------------------------------------------------------


def _resolve_lut_volume_path(volume_path: str, gui_state: dict | None) -> str:
    """Resolve an allowed viewer volume path to a local file for LUT filtering.

    Parameters
    ----------
    volume_path : str
        User-provided /case or output-root-relative volume path.
    gui_state : dict | None
        Current GUI state used to resolve /case paths.

    Returns
    -------
    str
        Absolute local path to an existing volume file.
    """
    raw = str(volume_path or "").strip()
    if not raw:
        raise ValueError("volume_path is required.")

    if raw == CONTAINER_CASE_ROOT or raw.startswith(f"{CONTAINER_CASE_ROOT}/"):
        case_local_dir = _resolve_case_mount_local_dir(gui_state)
        if not case_local_dir:
            raise ValueError("/case paths require an active case in the GUI state.")
        relative_path = raw[len(CONTAINER_CASE_ROOT) :].lstrip("/")
        candidate = os.path.join(case_local_dir, *PurePosixPath(relative_path).parts)
        root = os.path.realpath(case_local_dir)
    elif raw.startswith("/"):
        raise ValueError("Only /case and output-root-relative volume paths are allowed.")
    else:
        candidate = os.path.join(LOCAL_OUTPUT_ROOT, *PurePosixPath(raw).parts)
        root = os.path.realpath(LOCAL_OUTPUT_ROOT)

    resolved = os.path.realpath(candidate)
    if os.path.commonpath([resolved, root]) != root:
        raise ValueError("volume_path escapes the allowed data root.")
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"volume_path not found on disk: {raw}")
    return resolved


def _label_ids_in_volume(volume_path: str) -> set[int]:
    """Read unique integer label IDs from a neuroimaging volume.

    Parameters
    ----------
    volume_path : str
        Local path to the volume to inspect.

    Returns
    -------
    set[int]
        Integer voxel values present in the volume.
    """
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
    """Look up FreeSurfer label IDs by name (partial/case-insensitive) or name by ID."""
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
            volume_context = (
                f"Filtered to {len(available_label_ids)} unique integer label ID(s) "
                f"present in {volume_path}."
            )
        except Exception as exc:
            return error_response(f"inspecting volume_path '{volume_path}': {exc}")

    results: list[str] = []

    # Try numeric ID lookup first
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
        # Ranked name/comment search
        matches, total = search_lut(query, limit=50, allowed_label_ids=available_label_ids)
        if matches:
            results = [
                f"{match.label_id}\t{match.name}"
                + (f"\t# {match.comment}" if match.comment else "")
                for match in matches
            ]
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


def _surface_parts(filename: str) -> tuple[str, str] | None:
    parts = filename.split(".")
    if len(parts) == 2 and parts[0] in {"lh", "rh"} and parts[1] in SURFACE_ROLES:
        return parts[0], parts[1]
    return None


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
        return error_response(
            "gui_load_layer supports MRI volumes and FreeSurfer surface meshes, "
            f"not '{filename}'."
        )

    current_case_path = _current_case_relative_output_path(gui_state)
    if not current_case_path:
        return error_response("/case paths require an active case in the GUI state.")
    relative_path = PurePosixPath(file_path.removeprefix(f"{CONTAINER_CASE_ROOT}/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return error_response("file_path must stay inside the active /case directory.")
    file_path = f"{current_case_path}/{'/'.join(relative_path.parts)}"
    descriptor_path = output_descriptor_path_from_file(file_path)

    # Validate the file exists on disk before telling the frontend to load it
    disk_path = os.path.join(LOCAL_OUTPUT_ROOT, file_path)
    if not os.path.isfile(disk_path):
        return error_response(
            f"file not found on disk: {file_path}. Check the exact filename with case_file_tree."
        )

    display_name = name or filename.replace(".mgz", "").replace(".nii.gz", "")

    # Classify type heuristically
    is_seg = _looks_like_segmentation(filename)

    # Brainmask files and *_bin volumes are binary (0=background, 1=structure)
    is_binary_mask = (
        any(k in filename.lower() for k in ("mask", "brainmask"))
        or "_bin" in filename.lower()
    )
    lut_type = "binary" if is_binary_mask else ("freesurfer" if is_seg else None)

    layer_type = "surface" if surface_parts else ("segmentation" if is_seg else "intensity")
    load_cmd: dict[str, Any] = {
        "resource": output_resource_descriptor(descriptor_path),
        "filename": filename,
        "name": display_name,
        "type": layer_type,
    }
    if lut_type:
        load_cmd["lut"] = lut_type
    if isinstance(visible, bool):
        load_cmd["visible"] = visible

    if surface_parts:
        hemisphere, role = surface_parts
        load_cmd["hemisphere"] = "left" if hemisphere == "lh" else "right"
        load_cmd["role"] = role
        case_prefix = str(PurePosixPath(file_path).parent.parent)
        curvature_path = f"{case_prefix}/surf/{hemisphere}.curv"
        annotation_path = f"{case_prefix}/label/{hemisphere}.aparc.DKTatlas.mapped.annot"
        for resource_key, companion_path in (
            ("curvature_resource", curvature_path),
            ("annotation_resource", annotation_path),
        ):
            companion_disk_path = os.path.join(LOCAL_OUTPUT_ROOT, companion_path)
            if os.path.isfile(companion_disk_path):
                load_cmd[resource_key] = output_resource_descriptor(
                    output_descriptor_path_from_file(companion_path)
                )

    command_id = enqueue_gui_command(gui_state, "load_layer", load_cmd)
    return text_response(
        f"Queued GUI command {command_id} to load {layer_type} layer "
        f"'{display_name}' ({descriptor_path})."
    )


def _requested_focus_label_segmentation(arguments: dict) -> str | None:
    """Return the first supported segmentation path argument, if provided.

    Parameters
    ----------
    arguments : dict
        Tool arguments that may include a segmentation file/path alias.

    Returns
    -------
    str | None
        Requested segmentation path, or None when no alias was supplied.
    """
    for key in ("segmentation_file", "segmentation_volume", "segmentation_path", "segmentation_map"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_focus_label_segmentation_spec(arguments: dict, gui_state: dict) -> tuple[dict | None, str | None]:
    """Resolve the segmentation volume used by gui_focus_label.

    Parameters
    ----------
    arguments : dict
        Tool arguments containing optional segmentation path overrides.
    gui_state : dict
        Current GUI state containing the active case output path.

    Returns
    -------
    tuple[dict | None, str | None]
        Resolved segmentation metadata, or an error message when invalid.
    """
    current_case_path = _current_case_relative_output_path(gui_state)
    if not current_case_path:
        return None, "Error: No case is loaded. Upload an MRI first."

    case_root = os.path.join(LOCAL_OUTPUT_ROOT, current_case_path)
    requested_value = _requested_focus_label_segmentation(arguments)
    raw_value = requested_value or FOCUS_LABEL_DEFAULT_SEGMENTATION
    normalized_value = str(raw_value).strip()
    if not normalized_value:
        normalized_value = FOCUS_LABEL_DEFAULT_SEGMENTATION

    if normalized_value.startswith(f"{CONTAINER_CASE_ROOT}/"):
        relative_path = normalized_value[len(f"{CONTAINER_CASE_ROOT}/") :].strip("/")
        disk_path = os.path.join(case_root, *PurePosixPath(relative_path).parts)
        display_path = f"{CONTAINER_CASE_ROOT}/{relative_path}" if relative_path else CONTAINER_CASE_ROOT
    elif normalized_value.startswith(f"{current_case_path}/"):
        relative_path = normalized_value[len(current_case_path) :].strip("/")
        disk_path = os.path.join(case_root, *PurePosixPath(relative_path).parts)
        display_path = f"{current_case_path}/{relative_path}" if relative_path else current_case_path
    else:
        path_obj = PurePosixPath(normalized_value)
        if path_obj.is_absolute():
            return None, (
                "Error: segmentation_file must be a filename, a current-case relative path like "
                "`mri/aseg.auto_noCCseg.mgz`, `/case/...`, or a readable `workspaces/<workspace-slug>/cases/<case-slug>/...` path."
            )
        relative_path = "/".join(path_obj.parts)
        if not relative_path:
            return None, "Error: segmentation_file must not be empty."
        if "/" not in relative_path:
            relative_path = f"mri/{relative_path}"
        disk_path = os.path.join(case_root, *PurePosixPath(relative_path).parts)
        display_path = f"{current_case_path}/{relative_path}"

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


def _list_case_segmentation_volumes(current_case_path: str) -> list[str]:
    """List segmentation-like volume files under the active case mri directory.

    Parameters
    ----------
    current_case_path : str
        Output-root-relative path for the active case.

    Returns
    -------
    list[str]
        Case-relative segmentation volume paths.
    """
    case_root = os.path.join(LOCAL_OUTPUT_ROOT, current_case_path)
    mri_dir = os.path.join(case_root, "mri")
    if not os.path.isdir(mri_dir):
        return []

    volumes: list[str] = []
    for root, _dirs, filenames in os.walk(mri_dir):
        for filename in sorted(filenames):
            if not filename.lower().endswith(_VOLUME_FILE_SUFFIXES):
                continue
            if not _looks_like_segmentation(filename):
                continue
            rel_path = os.path.relpath(os.path.join(root, filename), case_root).replace(os.sep, "/")
            volumes.append(rel_path)
    return sorted(set(volumes))


def _format_focus_label_missing_segmentation(
    segmentation_spec: dict,
    current_case_path: str,
) -> str:
    """Build a user-facing error for a missing focus-label segmentation file.

    Parameters
    ----------
    segmentation_spec : dict
        Resolved segmentation metadata from _resolve_focus_label_segmentation_spec.
    current_case_path : str
        Output-root-relative path for the active case.

    Returns
    -------
    str
        Error message with available segmentation alternatives when known.
    """
    available = _list_case_segmentation_volumes(current_case_path)
    if available:
        available_text = "Available segmentation volumes in this case: " + ", ".join(
            f"`{path}`" for path in available
        ) + "."
    else:
        available_text = (
            "No segmentation volumes were found under "
            "`/case/mri/`."
        )

    if segmentation_spec["used_default"]:
        return (
            f"Failed to focus label: default segmentation `{FOCUS_LABEL_DEFAULT_SEGMENTATION}` "
            f"was not found for the current case (looked for "
            f"`{segmentation_spec['display_path']}`). {available_text} "
            "Pass `segmentation_file` to `gui_focus_label` to use a different segmentation map."
        )

    return (
        f"Failed to focus label: segmentation file `{segmentation_spec['requested_value']}` "
        f"was not found in the current case (resolved to "
        f"`{segmentation_spec['display_path']}`). {available_text}"
    )


def _is_supported_viewer_path(file_path: str) -> bool:
    """Check whether a path has a suffix the viewer can load.

    Parameters
    ----------
    file_path : str
        Candidate path or filename.

    Returns
    -------
    bool
        True when the path extension is supported by the viewer.
    """
    lowered = file_path.lower()
    return lowered.endswith(ALLOWED_VIEWER_SUFFIXES)


def handle_gui_list_layers(_arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Return the typed viewer-layer snapshot."""
    layers = _layers(gui_state)
    if not layers:
        return text_response("No layers are currently loaded in the viewer.")
    rows = [
        {
            "id": _layer_id(layer),
            "filename": layer.get("filename"),
            "type": layer.get("type"),
            "role": layer.get("role"),
            "hemisphere": layer.get("hemisphere"),
            "visible": bool(layer.get("visible")),
            "opacity": layer.get("opacity"),
            "display": layer.get("display") or {},
        }
        for layer in layers
    ]
    return text_response(json.dumps(rows, ensure_ascii=True))


def handle_gui_remove_layer(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Queue removal of one or more loaded layers."""
    layer_ids = [str(value) for value in arguments.get("layer_ids", []) if value]
    if not layer_ids:
        return error_response("layer_ids must contain at least one layer identifier.")
    missing = [layer_id for layer_id in layer_ids if _find_layer(gui_state, layer_id) is None]
    if missing:
        return error_response("Layer(s) are not loaded: " + ", ".join(missing))
    command_id = enqueue_gui_command(gui_state, "remove_layers", {"layer_ids": layer_ids})
    return text_response(f"Queued GUI command {command_id} to remove: {', '.join(layer_ids)}.")


def handle_gui_set_layer_visibility(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Queue atomic visibility changes across any layer type."""
    changes = arguments.get("changes")
    if not isinstance(changes, list) or not changes:
        return error_response("changes must contain at least one layer visibility update.")
    normalized: list[dict[str, Any]] = []
    missing: list[str] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        requested_id = str(change.get("layer_id") or "")
        layer = _find_layer(gui_state, requested_id)
        if layer is None:
            missing.append(requested_id)
            continue
        normalized.append({"layer_id": _layer_id(layer), "visible": bool(change.get("visible"))})
    if missing:
        return error_response("Layer(s) are not loaded: " + ", ".join(missing))
    if not normalized:
        return error_response("No valid layer visibility changes were provided.")
    command_id = enqueue_gui_command(
        gui_state,
        "set_layer_visibility",
        {"changes": normalized},
    )
    return text_response(
        f"Queued GUI command {command_id} with {len(normalized)} layer visibility change(s)."
    )


def handle_gui_set_layer_display(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Queue targeted display changes for volumes or surfaces."""
    layer_ids = [str(value) for value in arguments.get("layer_ids", []) if value]
    if not layer_ids:
        return error_response("layer_ids must contain at least one layer identifier.")
    missing = [layer_id for layer_id in layer_ids if _find_layer(gui_state, layer_id) is None]
    if missing:
        return error_response("Layer(s) are not loaded: " + ", ".join(missing))
    target_layers = [
        layer
        for layer_id in layer_ids
        if (layer := _find_layer(gui_state, layer_id)) is not None
    ]
    has_surface = any(layer.get("type") == "surface" for layer in target_layers)
    has_volume = any(layer.get("type") != "surface" for layer in target_layers)
    if has_surface and ("brightness" in arguments or "contrast" in arguments):
        return error_response("brightness and contrast apply only to intensity or segmentation layers.")
    if has_volume and "surface_color_mode" in arguments:
        return error_response("surface_color_mode applies only to surface layers.")

    updates: dict[str, Any] = {}
    if "opacity" in arguments:
        updates["opacity"] = max(0.0, min(1.0, float(arguments["opacity"])))
    if "brightness" in arguments:
        updates["brightness"] = max(-100.0, min(100.0, float(arguments["brightness"])))
    if "contrast" in arguments:
        updates["contrast"] = max(0.0, min(3.0, float(arguments["contrast"])))
    if "surface_color_mode" in arguments:
        mode = str(arguments["surface_color_mode"])
        if mode not in {"solid", "curvature", "annotation"}:
            return error_response("surface_color_mode must be solid, curvature, or annotation.")
        updates["surface_color_mode"] = mode
    if not updates:
        return error_response(
            "Provide opacity, brightness, contrast, or surface_color_mode."
        )

    command_id = enqueue_gui_command(
        gui_state,
        "set_layer_display",
        {"layer_ids": layer_ids, "updates": updates},
    )
    return text_response(
        f"Queued GUI command {command_id} to update {', '.join(layer_ids)}."
    )


def _preset_visibility_changes(gui_state: dict, preset: str) -> list[dict[str, Any]]:
    layers = _layers(gui_state)
    intensity_layers = [layer for layer in layers if layer.get("type") == "intensity"]
    current_intensity = str(gui_state.get("current_intensity_volume") or "").lower()
    selected_intensity = next(
        (
            layer
            for layer in intensity_layers
            if str(layer.get("filename") or "").lower() == current_intensity
        ),
        intensity_layers[0] if intensity_layers else None,
    )
    segmentation_layers = [layer for layer in layers if layer.get("type") == "segmentation"]
    selected_segmentation = next(
        (
            layer
            for preferred in (
                "aparc.dktatlas+aseg.deep.mgz",
                "aparc.dktatlas+aseg.mgz",
            )
            for layer in segmentation_layers
            if str(layer.get("filename") or "").lower().endswith(preferred)
        ),
        next(
            (
                layer
                for layer in segmentation_layers
                if "aparc" in str(layer.get("filename") or "").lower()
                and "aseg" in str(layer.get("filename") or "").lower()
            ),
            None,
        ),
    )
    changes: list[dict[str, Any]] = []
    for layer in layers:
        layer_type = str(layer.get("type") or "intensity")
        role = str(layer.get("role") or "")
        desired: bool | None = None
        if preset == "intensity_only" or layer_type == "intensity":
            desired = layer is selected_intensity
        elif layer_type == "segmentation":
            desired = preset in {
                "whole_brain_segmentation",
                "segmentation_with_pial_surfaces",
                "segmentation_with_surfaces",
            } and layer is selected_segmentation
        elif layer_type == "surface":
            if preset in {"pial_surfaces", "segmentation_with_pial_surfaces"}:
                desired = role == "pial"
            elif preset == "white_surfaces":
                desired = role == "white"
            elif preset in {"cortical_surfaces", "segmentation_with_surfaces"}:
                desired = role in {"pial", "white"}
        if desired is not None:
            changes.append({"layer_id": _layer_id(layer), "visible": desired})
    return changes


def handle_gui_apply_view_preset(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Resolve a semantic viewer preset into one atomic visibility command."""
    preset = str(arguments.get("preset") or "")
    supported = {
        "intensity_only",
        "whole_brain_segmentation",
        "pial_surfaces",
        "white_surfaces",
        "cortical_surfaces",
        "segmentation_with_pial_surfaces",
        "segmentation_with_surfaces",
    }
    if preset not in supported:
        return error_response("Unsupported preset. Choose one of: " + ", ".join(sorted(supported)))
    changes = _preset_visibility_changes(gui_state, preset)
    target_matches = {
        "intensity_only": lambda layer: layer.get("type") == "intensity",
        "whole_brain_segmentation": lambda layer: layer.get("type") == "segmentation",
        "pial_surfaces": lambda layer: layer.get("type") == "surface" and layer.get("role") == "pial",
        "white_surfaces": lambda layer: layer.get("type") == "surface" and layer.get("role") == "white",
        "cortical_surfaces": lambda layer: layer.get("type") == "surface" and layer.get("role") in {"pial", "white"},
        "segmentation_with_pial_surfaces": lambda layer: (
            layer.get("type") == "segmentation"
            or (layer.get("type") == "surface" and layer.get("role") == "pial")
        ),
        "segmentation_with_surfaces": lambda layer: (
            layer.get("type") == "segmentation"
            or (layer.get("type") == "surface" and layer.get("role") in {"pial", "white"})
        ),
    }
    has_target = any(target_matches[preset](layer) for layer in _layers(gui_state))
    if not changes or not has_target:
        return error_response(f"Preset '{preset}' has no matching loaded layers.")
    command_id = enqueue_gui_command(
        gui_state,
        "set_layer_visibility",
        {"changes": changes, "preset": preset},
    )
    return text_response(
        f"Queued GUI command {command_id} to apply view preset '{preset}' "
        f"across {len(changes)} layer(s)."
    )


def handle_gui_run_fastsurfer(
    arguments: dict, gui_state: dict
) -> list[ToolTextContent]:
    """Validate the active case and request a FastSurfer run from the frontend.

    Parameters
    ----------
    arguments : dict
        Tool arguments, including optional case name and seg-only mode.
    gui_state : dict
        Current GUI state used to verify the active case and running jobs.

    Returns
    -------
    list[ToolTextContent]
        Success or validation error message for the runtime tool call.
    """
    if gui_state.get("is_job_running"):
        return error_response("A run is already active in the GUI. Cannot start a new one.")

    current_case_id = gui_state.get("current_case_id")
    if not current_case_id:
        return error_response(
            "No image is loaded in the viewer. The user must upload an MRI file first before you can trigger a FastSurfer run."
        )

    input_artifact_id = gui_state.get("current_intensity_artifact_id")
    input_volume_name = gui_state.get("current_intensity_volume")

    case_name = arguments.get("case_name", "")
    run_request = {
        "case_id": current_case_id,
        "seg_only": arguments.get("seg_only", True),
        "case_name": case_name,
    }
    if input_artifact_id:
        run_request["input_artifact_id"] = input_artifact_id
    if input_volume_name:
        run_request["input_volume"] = input_volume_name
    command_id = enqueue_gui_command(gui_state, "run_fastsurfer", run_request)
    if input_artifact_id:
        gui_state["is_job_running"] = True

    display_name = case_name if case_name else current_case_id
    if not input_artifact_id:
        return text_response(
            f"Queued GUI command {command_id} for FastSurfer case '{display_name}'. "
            "The frontend will ask the user to choose an input layer."
        )
    return text_response(
        f"Queued GUI command {command_id} to run FastSurfer for case '{display_name}'."
    )


def handle_gui_move_cursor(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Request that the frontend move the viewer cursor to voxel coordinates.

    Parameters
    ----------
    arguments : dict
        Tool arguments containing x, y, and z coordinates.
    gui_state : dict
        Current GUI state updated with the requested cursor position.

    Returns
    -------
    list[ToolTextContent]
        Confirmation message for the cursor move request.
    """
    if not _layers(gui_state):
        return _missing_layer_error("gui_move_cursor")
    x = arguments.get("x")
    y = arguments.get("y")
    z = arguments.get("z")
    command_id = enqueue_gui_command(
        gui_state,
        "move_cursor",
        {"position": [x, y, z]},
    )
    return text_response(
        f"Queued GUI command {command_id} to move the cursor to ({x}, {y}, {z})."
    )


def handle_gui_focus_label(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    # Accept both "label_query" (correct) and "label_name" (common LLM hallucination)
    """Find a segmentation label centroid and request the viewer cursor move there.

    Parameters
    ----------
    arguments : dict
        Tool arguments containing a label query and optional segmentation file.
    gui_state : dict
        Current GUI state used to resolve case output and store cursor movement.

    Returns
    -------
    list[ToolTextContent]
        Success or error message from resolving and focusing the label.
    """
    label_query = arguments.get("label_query") or arguments.get("label_name")
    if not label_query:
        return error_response("Missing label_query argument.")

    current_case_path = _current_case_relative_output_path(gui_state)
    if not current_case_path:
        return error_response("No case is loaded. Upload an MRI first.")

    segmentation_spec, segmentation_error = _resolve_focus_label_segmentation_spec(arguments, gui_state)
    if segmentation_error:
        return error_response(segmentation_error)
    if segmentation_spec is None:
        return error_response("Unable to resolve a segmentation file for the active case.")
    if not os.path.isfile(segmentation_spec["disk_path"]):
        return error_response(_format_focus_label_missing_segmentation(segmentation_spec, current_case_path))

    # Run centroid calculation directly in-process (nibabel + numpy are
    # available in this process) instead of spawning a nested runtime.
    from .focus_label import get_label_centroid

    lut_path = str(ROOT_DIR / "config" / "FreeSurferColorLUT.txt")

    try:
        data = get_label_centroid(segmentation_spec["disk_path"], str(label_query), lut_path)

        if "error" in data:
            return text_response(f"Failed to focus label: {data['error']}")

        x, y, z = data["x"], data["y"], data["z"]
        command_id = enqueue_gui_command(
            gui_state,
            "move_cursor",
            {"position": [x, y, z]},
        )

        return text_response(
            f"Found centroid for {data['label_name']} ({data['label_id']}) and queued "
            f"GUI command {command_id} to move the cursor to ({x}, {y}, {z})."
        )
    except Exception as e:
        return error_response(f"An unexpected error occurred during label focus: {str(e)}")
