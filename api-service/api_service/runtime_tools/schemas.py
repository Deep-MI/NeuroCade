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
            description="List the active case files under /case to discover exact runtime paths.",
            input_schema={"type": "object", "properties": {}},
        ),
        RuntimeToolSpec(
            name="gui_list_layers",
            description=(
                "Return the current typed viewer layers with IDs, filenames, types, roles, "
                "hemispheres, visibility, opacity, and display configuration. Use this before "
                "targeted layer changes when the exact layer ID is uncertain."
            ),
            input_schema={"type": "object", "properties": {}},
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
            description="Read FastSurfer volumetric statistics for the active or specified case.",
            input_schema={
                "type": "object",
                "properties": {
                    "label_query": {"type": "string"},
                    "stats_file": {"type": "string"},
                    "case_id": {"type": "string"},
                },
            },
        ),
        RuntimeToolSpec(
            name="gui_run_fastsurfer",
            description=(
                "Request FastSurfer for the active case. Surface reconstruction is enabled by "
                "setting seg_only=false."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "case_name": {"type": "string"},
                    "seg_only": {"type": "boolean", "default": True},
                },
            },
        ),
    ]
