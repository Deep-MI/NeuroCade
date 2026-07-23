"""Provide API service handlers behavior for NeuroCade."""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any, cast

from dotenv import load_dotenv

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
from .read_stats import handle_read_stats as handle_read_stats
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


def _loaded_volume_names(gui_state: dict) -> list[str]:
    """Return loaded viewer volume display names from GUI state."""
    values = gui_state.get("loaded_volume_names") or gui_state.get("loaded_volumes") or []
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if isinstance(value, str) and value]


def _missing_loaded_volume_error(action: str) -> list[ToolTextContent]:
    """Return a standard error for viewer actions that require a loaded volume."""
    return error_response(f"{action} requires at least one loaded volume in the GUI. Load a volume first with gui_load_volume.")


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


def handle_gui_load_volume(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Request the frontend to load (or reload) a volume in the viewer."""
    file_path = str(arguments.get("file_path", "")).strip()
    name = arguments.get("name", "")
    visible = arguments.get("visible")

    if not file_path:
        return error_response("file_path is required.")

    if file_path.startswith("/") and not file_path.startswith(f"{CONTAINER_CASE_ROOT}/"):
        return error_response("gui_load_volume accepts /case paths or output-root-relative paths only.")

    if not _is_supported_viewer_path(file_path):
        return error_response(
            "gui_load_volume only supports viewer-compatible files "
            f"({', '.join(ALLOWED_VIEWER_SUFFIXES)}), not '{os.path.basename(file_path)}'."
        )

    # Determine resource path based on path prefix
    if file_path.startswith(f"{CONTAINER_CASE_ROOT}/"):
        current_case_path = _current_case_relative_output_path(gui_state)
        if not current_case_path:
            return error_response("/case paths require an active case in the GUI state.")
        relative_case_path = file_path[len(f"{CONTAINER_CASE_ROOT}/") :]
        file_path = f"{current_case_path}/{relative_case_path}"
        descriptor_path = output_descriptor_path_from_file(file_path)
    else:
        descriptor_path = output_descriptor_path_from_file(file_path)

    # Validate the file exists on disk before telling the frontend to load it
    disk_path = os.path.join(LOCAL_OUTPUT_ROOT, file_path) if descriptor_path.startswith("outputs/") else None
    if disk_path and not os.path.isfile(disk_path):
        return error_response(
            f"file not found on disk: {file_path}. Check the exact filename with the available_layers tool or case artifacts endpoint."
        )

    filename = os.path.basename(file_path)
    display_name = name or filename.replace(".mgz", "").replace(".nii.gz", "")

    # Classify type heuristically
    is_seg = _looks_like_segmentation(filename)

    # Brainmask files and *_bin volumes are binary (0=background, 1=structure)
    is_binary_mask = (
        any(k in filename.lower() for k in ("mask", "brainmask"))
        or "_bin" in filename.lower()
    )
    lut_type = "binary" if is_binary_mask else ("freesurfer" if is_seg else None)

    load_cmd: dict = {
        "resource": output_resource_descriptor(descriptor_path),
        "filename": filename,
        "name": display_name,
        "type": "segmentation" if is_seg else "intensity",
    }
    if lut_type:
        load_cmd["lut"] = lut_type
    if isinstance(visible, bool):
        load_cmd["visible"] = visible

    gui_state["requested_load_volume"] = load_cmd
    return text_response(f"Successfully requested frontend to LOAD_VOLUME: {display_name} ({descriptor_path}).")


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


def handle_gui_close_volume(
    arguments: dict, gui_state: dict
) -> list[ToolTextContent]:
    """Request the frontend to remove a volume from the viewer."""
    loaded_volumes = _loaded_volume_names(gui_state)
    if not loaded_volumes:
        return _missing_loaded_volume_error("gui_close_volume")
    volume_id = arguments.get("volume_id", "")
    if not volume_id:
        return error_response("volume_id is required.")
    if str(volume_id) not in loaded_volumes:
        return error_response(
            f"volume_id '{volume_id}' is not currently loaded. Loaded volumes: "
            + ", ".join(loaded_volumes)
        )

    close_requests = gui_state.setdefault("requested_close_volumes", [])
    if not isinstance(close_requests, list):
        close_requests = [close_requests]
        gui_state["requested_close_volumes"] = close_requests
    close_requests.append({"volume_id": volume_id})
    return text_response(f"Successfully requested frontend to CLOSE_VOLUME: {volume_id}.")


def handle_gui_select_volume(
    arguments: dict, gui_state: dict
) -> list[ToolTextContent]:
    """Request the frontend to change which intensity/segmentation volumes are visible."""
    loaded_volumes = _loaded_volume_names(gui_state)
    if not loaded_volumes:
        return _missing_loaded_volume_error("gui_select_volume")
    intensity = arguments.get("intensity_volume", "")
    segmentation = arguments.get("segmentation_volume", "")
    requested = [str(value) for value in (intensity, segmentation) if value]
    missing = [value for value in requested if value not in loaded_volumes]
    if missing:
        return error_response(
            "requested volume(s) are not currently loaded: "
            + ", ".join(missing)
            + ". Loaded volumes: "
            + ", ".join(loaded_volumes)
        )

    gui_state["requested_select_volumes"] = {
        "intensity_volume": intensity,
        "segmentation_volume": segmentation,
    }

    desc = f"intensity={intensity if intensity else 'none'}, segmentation={segmentation if segmentation else 'none'}"
    return text_response(f"Successfully requested frontend to SELECT_VOLUMES: {desc}.")


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
    gui_state["requested_run_fastsurfer"] = run_request
    if input_artifact_id:
        gui_state["is_job_running"] = True

    display_name = case_name if case_name else current_case_id
    if not input_artifact_id:
        return text_response(
            f"Successfully requested FastSurfer run for case '{display_name}'. The frontend will ask the user to choose an input volume before starting the analysis pipeline."
        )
    return text_response(
        f"Successfully triggered FastSurfer run for case '{display_name}'. The frontend will start the analysis pipeline and the user can monitor progress in the terminal panel."
    )


def handle_gui_review_segmentation(gui_state: dict) -> list[ToolTextContent]:
    """Request segmentation review after confirming a valid segmentation is loaded.

    Parameters
    ----------
    gui_state : dict
        Current GUI state containing segmentation validity.

    Returns
    -------
    list[ToolTextContent]
        Success or validation error message for the runtime tool call.
    """
    if not gui_state.get("has_valid_segmentation"):
        return error_response("No valid segmentation loaded in the GUI.")
    return text_response("Successfully triggered frontend event: REVIEW_SEGMENTATION.")


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
    if not _loaded_volume_names(gui_state):
        return _missing_loaded_volume_error("gui_move_cursor")
    x = arguments.get("x")
    y = arguments.get("y")
    z = arguments.get("z")
    gui_state["requested_cursor_position"] = [x, y, z]
    return text_response(f"Successfully requested frontend to MOVE_CURSOR to ({x}, {y}, {z}).")


def handle_gui_adjust_display(
    arguments: dict, gui_state: dict
) -> list[ToolTextContent]:
    """Request the frontend to adjust viewer display settings (opacity, brightness, contrast)."""
    if not _loaded_volume_names(gui_state):
        return _missing_loaded_volume_error("gui_adjust_display")
    payload: dict = {}
    parts: list[str] = []

    if "opacity" in arguments:
        val = max(0.0, min(1.0, float(arguments["opacity"])))
        payload["opacity"] = val
        parts.append(f"opacity={val}")

    if "brightness" in arguments:
        val = max(-100.0, min(100.0, float(arguments["brightness"])))
        payload["brightness"] = val
        parts.append(f"brightness={val}")

    if "contrast" in arguments:
        val = max(0.0, min(3.0, float(arguments["contrast"])))
        payload["contrast"] = val
        parts.append(f"contrast={val}")

    if not payload:
        return error_response("at least one of opacity, brightness, or contrast is required.")

    gui_state["requested_adjust_display"] = payload
    return text_response(f"Successfully requested frontend to ADJUST_DISPLAY: {', '.join(parts)}.")


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
        gui_state["requested_cursor_position"] = [x, y, z]

        return text_response(
            f"Successfully found centroid for {data['label_name']} ({data['label_id']}) and requested frontend to MOVE_CURSOR to ({x}, {y}, {z})."
        )
    except Exception as e:
        return error_response(f"An unexpected error occurred during label focus: {str(e)}")
