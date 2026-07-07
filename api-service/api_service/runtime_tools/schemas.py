"""Provide API service schemas behavior for NeuroCade."""

from .types import RuntimeToolSpec

# Static Tool Definitions
STATIC_TOOLS = [
    RuntimeToolSpec(
        name="freesurfer_lut",
        description=(
            "Look up FreeSurfer label IDs and names from the FreeSurferColorLUT. "
            "Use this BEFORE using mri_binarize --match to find the correct numeric IDs for brain regions. "
            "Supports ranked name and annotation search (e.g. 'cerebellum' or 'corpus callosum') "
            "or exact ID lookup (e.g. '7')."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Label name, anatomical search phrase, abbreviation, or numeric label ID to look up.",
                },
                "volume_path": {
                    "type": "string",
                    "description": (
                        "Optional segmentation volume path. When provided, LUT results are first filtered "
                        "to label IDs actually present in that volume. In case mode, prefer paths like "
                        "/case/mri/aparc+aseg.mgz or /case/mri/aparc.DKTatlas+aseg.deep.mgz."
                    ),
                },
            },
            "required": ["query"],
        },
    ),
]


def get_dynamic_gui_tools(gui_state: dict) -> list[RuntimeToolSpec]:
    """Return the stable case-scope runtime GUI tool set.

    UI-dependent availability is enforced inside tool handlers. Keeping these
    tools registered gives the assistant a stable vocabulary throughout a
    multi-round turn.
    """
    case_tools = []

    register_case_runtime_tools = True

    # --- Pipeline control ---
    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="gui_run_fastsurfer",
                description="Requests a FastSurfer run for the active case. If a current intensity volume is selected, the frontend can start directly; otherwise it will open the FastSurfer input selector for the user to choose and confirm an input volume. Only call this if the user asks to run the pipeline and there isn't one already running. By default only segmentation is run (no surface reconstruction). Set seg_only=false only if the user explicitly asks for surface reconstruction.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "case_name": {
                            "type": "string",
                            "description": "Optional slug name for a new output case, using only lowercase a-z, 0-9, and hyphen, 2-64 characters, starting and ending with an alphanumeric character (for example 'roger4' or 'patient-01'). If omitted, the active case name is reused.",
                        },
                        "seg_only": {
                            "type": "boolean",
                            "description": "If true (default), only run segmentation. Set to false to also run surface reconstruction (much slower).",
                            "default": True,
                        },
                    },
                },
            )
        )

    # --- Volume management (require a loaded case) ---
    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="case_file_tree",
                description=(
                    "Show the complete file tree for the currently active case. Use this before "
                    "routing or running generic command-line tools if you need to discover the exact "
                    "filenames or subdirectories available under the current /case mount."
                ),
                input_schema={
                    "type": "object",
                    "properties": {},
                },
            )
        )
        case_tools.append(
            RuntimeToolSpec(
                name="gui_load_volume",
                description=(
                    "Load or reload an MRI volume in the web viewer so the user can "
                    "inspect it. Use this after running a configured processing "
                    "tool to display the result. If the file is already loaded it will "
                    "be refreshed with the latest data on disk."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": (
                                "Path to the volume file. In case mode, prefer active-case paths "
                                "such as /case/mri/brainmask_2mm.mgz. Output-root-relative paths "
                                "such as workspaces/<workspace-slug>/cases/<case-slug>/mri/brainmask_2mm.mgz "
                                "are also accepted for persisted resources."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": "Optional human-readable display name for the layer panel.",
                        },
                    },
                    "required": ["file_path"],
                },
            )
        )

    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="gui_close_volume",
                description="Close and remove a volume from the web viewer to reduce visual clutter.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "volume_id": {
                            "type": "string",
                            "description": (
                                "The filename of the volume to close "
                                "(e.g. 'brainmask_2mm.mgz' or 'aparc.DKTatlas+aseg.deep.mgz')."
                            ),
                        }
                    },
                    "required": ["volume_id"],
                },
            )
        )
        case_tools.append(
            RuntimeToolSpec(
                name="gui_select_volume",
                description=(
                    "Select which intensity and segmentation volumes are visible in "
                    "the viewer — equivalent to clicking the checkboxes in the layer "
                    "panel. Both parameters are required but may be empty strings to "
                    "deselect all volumes of that type."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "intensity_volume": {
                            "type": "string",
                            "description": (
                                "Filename of the intensity volume to make visible "
                                "(e.g. 'orig.mgz'). Pass '' to deselect all intensity volumes."
                            ),
                        },
                        "segmentation_volume": {
                            "type": "string",
                            "description": (
                                "Filename of the segmentation volume to make visible "
                                "(e.g. 'aparc.DKTatlas+aseg.deep.mgz'). "
                                "Pass '' to deselect all segmentation volumes."
                            ),
                        },
                    },
                    "required": ["intensity_volume", "segmentation_volume"],
                },
            )
        )

        # Cursor movement — only needs *some* volume loaded
        case_tools.append(
            RuntimeToolSpec(
                name="gui_move_cursor",
                description="Moves the MRI viewer cursor to a specific 3D voxel coordinate. Useful when you want to point out a specific structure or anomaly to the user.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "z": {"type": "integer"},
                    },
                    "required": ["x", "y", "z"],
                },
            )
        )

    # --- Segmentation-specific tools ---
    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="gui_review_segmentation",
                description="Review the active whole-brain segmentation mask (aparc.DKTatlas over T1) in the GUI viewer.",
                input_schema={"type": "object", "properties": {}},
            )
        )

    # read_stats and focus_label both read output files from disk — gate on
    # has_case so they become available as soon as a case has output files.
    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="read_stats",
                description=(
                    "Read pre-computed volumetric statistics from a FastSurfer .stats file. "
                    "FastSurfer writes stats/aseg+DKT.stats after every run — use this tool "
                    "instead of mri_segstats to get structure volumes, voxel counts, and "
                    "global brain measures without spawning a container runtime. "
                    "Supports ranked label lookup by name, annotation phrase, abbreviation, or numeric ID."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "label_query": {
                            "type": "string",
                            "description": (
                                "Optional. Structure name or anatomical phrase (e.g. "
                                "'hippocampus', 'corpus callosum', or 'Left-Thalamus') "
                                "or numeric label ID (e.g. '17'). "
                                "Also matches global measure names (e.g. 'BrainSeg', 'TotalGray'). "
                                "Omit to return all structures and global measures."
                            ),
                        },
                        "stats_file": {
                            "type": "string",
                            "description": (
                                "Stats filename inside the case's stats/ directory. "
                                "Defaults to 'aseg+DKT.stats'. "
                                "Other options: 'hypothalamus.HypVINN.stats'."
                            ),
                        },
                        "case_id": {
                            "type": "string",
                            "description": "Case ID. Defaults to the currently active case.",
                        },
                    },
                },
            )
        )

    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="gui_focus_label",
                description="Moves the structural viewer to the physical center of mass of a specific anatomical segmentation label or region. Reads the segmentation file directly from disk — does NOT require the segmentation to be loaded in the viewer.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "label_query": {
                            "type": "string",
                            "description": "The name, anatomical phrase, abbreviation, or integer ID of the anatomical section (e.g., 'Left-Hippocampus', 'corpus callosum', 'CC', or '17')",
                        },
                        "segmentation_file": {
                            "type": "string",
                            "description": (
                                "Optional segmentation filename or current-case path to use instead of the default "
                                "`aparc.DKTatlas+aseg.deep.mgz`. Examples: `aseg.auto_noCCseg.mgz`, "
                                "`mri/aseg.auto_noCCseg.mgz`, `/case/mri/aseg.auto_noCCseg.mgz`."
                            ),
                        },
                    },
                    "required": ["label_query"],
                },
            )
        )

    # --- Viewer display adjustments (require loaded volumes) ---
    if register_case_runtime_tools:
        case_tools.append(
            RuntimeToolSpec(
                name="gui_adjust_display",
                description=(
                    "Adjust the visual display settings of the MRI viewer. "
                    "All parameters are optional — include only the ones you want to change. "
                    "Opacity controls segmentation overlay transparency. "
                    "Brightness and contrast control the intensity (base) image rendering."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "opacity": {
                            "type": "number",
                            "description": "Segmentation overlay opacity from 0.0 (fully transparent) to 1.0 (fully opaque). Default is 0.7.",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "brightness": {
                            "type": "number",
                            "description": "Intensity image brightness offset from -100 (darkest) to 100 (brightest). Default is 0.",
                            "minimum": -100,
                            "maximum": 100,
                        },
                        "contrast": {
                            "type": "number",
                            "description": "Intensity image contrast multiplier from 0.0 (minimum) to 3.0 (maximum). Default is 1.0.",
                            "minimum": 0.0,
                            "maximum": 3.0,
                        },
                    },
                },
            )
        )

    return case_tools
