"""Provide API service handlers behavior for NeuroCade."""

from __future__ import annotations

import json
from typing import Any

from api_service.runtime.gui_state import enqueue_gui_command

from .types import ToolTextContent, error_response, text_response


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


def handle_gui_list_layers(_arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Return the typed viewer-layer snapshot."""
    layers = _layers(gui_state)
    if not layers:
        return text_response("No layers are currently loaded in the viewer.")
    type_counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for layer in layers:
        layer_type = str(layer.get("type") or "intensity")
        type_order = type_counts.get(layer_type, 0)
        type_counts[layer_type] = type_order + 1
        rows.append({
            "id": _layer_id(layer),
            "filename": layer.get("filename"),
            "type": layer_type,
            "type_order": type_order,
            "role": layer.get("role"),
            "hemisphere": layer.get("hemisphere"),
            "visible": bool(layer.get("visible")),
            "opacity": layer.get("opacity"),
            "display": layer.get("display") or {},
        })
    return text_response(json.dumps(rows, ensure_ascii=True))


def handle_gui_command_status(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Report whether a queued frontend command is pending or acknowledged."""
    command_id = str(arguments.get("command_id") or "").strip()
    if not command_id:
        return error_response("command_id is required.")
    if any(command.get("id") == command_id for command in gui_state.get("commands", [])):
        return text_response(json.dumps({"command_id": command_id, "status": "pending"}))
    completed = next(
        (
            command
            for command in reversed(gui_state.get("acknowledged_commands", []))
            if command.get("id") == command_id
        ),
        None,
    )
    if completed is not None:
        return text_response(json.dumps({
            "command_id": command_id,
            "status": "acknowledged",
            "acknowledged_at": completed.get("acknowledged_at"),
        }))
    return text_response(json.dumps({"command_id": command_id, "status": "unknown"}))


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


def handle_gui_reorder_layer(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Queue one same-type layer move relative to another layer."""
    layer_type = str(arguments.get("layer_type") or "")
    layer_id = str(arguments.get("layer_id") or "")
    target_layer_id = str(arguments.get("target_layer_id") or "")
    position = str(arguments.get("position") or "")
    if layer_type not in {"intensity", "segmentation", "surface"}:
        return error_response("layer_type must be intensity, segmentation, or surface.")
    if position not in {"before", "after"}:
        return error_response("position must be before or after.")
    if not layer_id or not target_layer_id:
        return error_response("layer_id and target_layer_id are required.")
    if layer_id == target_layer_id:
        return error_response("layer_id and target_layer_id must identify different layers.")

    layer = _find_layer(gui_state, layer_id)
    target_layer = _find_layer(gui_state, target_layer_id)
    if layer is None or target_layer is None:
        missing = [
            requested
            for requested, resolved in ((layer_id, layer), (target_layer_id, target_layer))
            if resolved is None
        ]
        return error_response("Layer(s) are not loaded: " + ", ".join(missing))
    if layer.get("type") != layer_type or target_layer.get("type") != layer_type:
        return error_response(
            f"Both layers must be loaded {layer_type} layers. Cross-type reordering is not allowed."
        )

    payload = {
        "layer_type": layer_type,
        "layer_id": _layer_id(layer),
        "target_layer_id": _layer_id(target_layer),
        "position": position,
    }
    command_id = enqueue_gui_command(gui_state, "reorder_layer", payload)
    return text_response(
        f"Queued GUI command {command_id} to move '{payload['layer_id']}' "
        f"{position} '{payload['target_layer_id']}' within {layer_type} layers."
    )


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
