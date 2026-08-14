"""Stable assistant runtime tool schemas."""

from .types import RuntimeToolSpec

STATIC_TOOLS = [
    RuntimeToolSpec(
        name="freesurfer_lut",
        description=(
            "Look up FreeSurfer label IDs and names, optionally restricted to labels "
            "present in a segmentation volume."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "volume_path": {"type": "string"},
            },
            "required": ["query"],
        },
    ),
]


def get_dynamic_gui_tools(_gui_state: dict) -> list[RuntimeToolSpec]:
    """Return the stable case and typed-viewer tool vocabulary."""
    return [
        RuntimeToolSpec(
            name="case_file_tree",
            description=(
                "List a bounded active-case file tree to discover exact runtime paths. "
                "Set path to a directory such as mri, surf, or scripts/runs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "default": 500},
                },
            },
        ),
        RuntimeToolSpec(
            name="gui_list_layers",
            description=(
                "Return the current typed viewer layers with IDs, filenames, types, roles, "
                "hemispheres, visibility, opacity, display configuration, and zero-based order "
                "within each layer type. Use this before targeted layer changes."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        RuntimeToolSpec(
            name="gui_command_status",
            description=(
                "Check whether a GUI command is still pending or has been acknowledged by the browser. "
                "A queued command must not be described as applied until this reports acknowledged."
            ),
            input_schema={
                "type": "object",
                "properties": {"command_id": {"type": "string"}},
                "required": ["command_id"],
            },
        ),
        RuntimeToolSpec(
            name="gui_load_layer",
            description=(
                "Load or refresh an MRI intensity, segmentation, or FreeSurfer surface layer. "
                "Surface curvature and DKT annotation companions are resolved automatically."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Active-case path such as /case/mri/orig.mgz or /case/surf/lh.pial."
                        ),
                    },
                    "name": {"type": "string"},
                    "visible": {"type": "boolean", "default": True},
                },
                "required": ["file_path"],
            },
        ),
        RuntimeToolSpec(
            name="gui_set_layer_visibility",
            description=(
                "Atomically show or hide any loaded intensity, segmentation, or surface layers."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layer_id": {"type": "string"},
                                "visible": {"type": "boolean"},
                            },
                            "required": ["layer_id", "visible"],
                        },
                        "minItems": 1,
                    }
                },
                "required": ["changes"],
            },
        ),
        RuntimeToolSpec(
            name="gui_set_layer_display",
            description=(
                "Change display properties for specific loaded layers: opacity, volume "
                "brightness/contrast, or surface coloring mode."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "layer_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                    "brightness": {"type": "number", "minimum": -100, "maximum": 100},
                    "contrast": {"type": "number", "minimum": 0, "maximum": 3},
                    "surface_color_mode": {
                        "type": "string",
                        "enum": ["solid", "curvature", "annotation"],
                    },
                },
                "required": ["layer_ids"],
            },
        ),
        RuntimeToolSpec(
            name="gui_remove_layer",
            description="Unload one or more viewer layers.",
            input_schema={
                "type": "object",
                "properties": {
                    "layer_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
                "required": ["layer_ids"],
            },
        ),
        RuntimeToolSpec(
            name="gui_reorder_layer",
            description=(
                "Move one loaded layer before or after another layer of the same type. "
                "Cross-type reordering is rejected. Use gui_list_layers first to obtain IDs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "layer_type": {
                        "type": "string",
                        "enum": ["intensity", "segmentation", "surface"],
                    },
                    "layer_id": {"type": "string"},
                    "target_layer_id": {"type": "string"},
                    "position": {
                        "type": "string",
                        "enum": ["before", "after"],
                    },
                },
                "required": [
                    "layer_type",
                    "layer_id",
                    "target_layer_id",
                    "position",
                ],
            },
        ),
        RuntimeToolSpec(
            name="gui_apply_view_preset",
            description=(
                "Apply a semantic, atomic viewer configuration. Prefer this for common review "
                "requests instead of manually changing several layer visibilities."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "preset": {
                        "type": "string",
                        "enum": [
                            "intensity_only",
                            "whole_brain_segmentation",
                            "pial_surfaces",
                            "white_surfaces",
                            "cortical_surfaces",
                            "segmentation_with_pial_surfaces",
                            "segmentation_with_surfaces",
                        ],
                    }
                },
                "required": ["preset"],
            },
        ),
        RuntimeToolSpec(
            name="gui_move_cursor",
            description="Move the MRI viewer cursor to explicit voxel coordinates.",
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "z": {"type": "integer"},
                },
                "required": ["x", "y", "z"],
            },
        ),
        RuntimeToolSpec(
            name="gui_focus_label",
            description=(
                "Find an anatomical segmentation label centroid and move the viewer cursor there."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "label_query": {"type": "string"},
                    "segmentation_file": {"type": "string"},
                },
                "required": ["label_query"],
            },
        ),
        RuntimeToolSpec(
            name="read_stats",
            description="Read FastSurfer volumetric statistics for the active case.",
            input_schema={
                "type": "object",
                "properties": {
                    "label_query": {"type": "string"},
                    "stats_file": {"type": "string"},
                },
            },
        ),
    ]
