"""Parse FreeSurfer/FastSurfer .stats files in-process.

The ``aseg+DKT.stats`` file is written by FastSurfer's ``segstats.py`` at the
end of every normal run.  This module reads it directly from disk so the agent
never needs to shell out to ``mri_segstats``.
"""

from __future__ import annotations

import os
from contextlib import suppress

from backend_common.case_storage import case_slug_from_id
from backend_common.settings import get_settings

from .lut import resolve_label
from .types import ToolTextContent

_OUTPUT_DIR = str(get_settings().outputs_dir)


def _case_output_root_from_ids(workspace_id: str, case_id: str) -> str | None:
    """Build an output-relative case root from path-safe immutable IDs."""
    if not workspace_id or not case_id:
        return None
    if any(separator in workspace_id or separator in case_id for separator in ("/", "\\")):
        return None
    if workspace_id in {".", ".."} or case_id in {".", ".."}:
        return None
    return f"workspaces/{workspace_id}/cases/{case_slug_from_id(workspace_id, case_id)}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_stats_file(path: str) -> dict:
    """Parse a FreeSurfer/FastSurfer ``.stats`` file.

    Returns a dict with two keys:
    - ``measures``: global summary values from ``# Measure ...`` header lines
    - ``rows``:     per-structure rows (Index, SegId, NVoxels, Volume_mm3, StructName)
    """
    measures: list[dict] = []
    rows: list[dict] = []

    with open(path) as fh:
        for line in fh:
            line = line.rstrip()

            # Global measure lines:
            # "# Measure BrainSeg, BrainSegVol, Brain Segmentation Volume, 1259669.999987, mm^3"
            if line.startswith("# Measure "):
                parts = line[len("# Measure ") :].split(",")
                if len(parts) >= 4:
                    with suppress(ValueError, IndexError):
                        measures.append(
                            {
                                "name": parts[0].strip(),
                                "long_name": parts[1].strip(),
                                "description": parts[2].strip(),
                                "value": float(parts[3].strip()),
                                "unit": parts[4].strip() if len(parts) > 4 else "",
                            }
                        )
                continue

            # Skip all other comment lines
            if line.startswith("#"):
                continue

            # Data rows: "  1   2  475086  247745.444  Left-Cerebral-White-Matter  ..."
            parts = line.split()
            if len(parts) >= 5:
                with suppress(ValueError, IndexError):
                    rows.append(
                        {
                            "index": int(parts[0]),
                            "seg_id": int(parts[1]),
                            "n_voxels": int(parts[2]),
                            "volume_mm3": float(parts[3]),
                            "struct_name": parts[4],
                        }
                    )

    return {"measures": measures, "rows": rows}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_rows(rows: list[dict]) -> str:
    """Format per-structure stats rows as a fixed-width table."""
    header = f"{'SegId':<8} {'NVoxels':<10} {'Volume_mm3':<14} StructName"
    lines = [header]
    for r in rows:
        lines.append(
            f"{r['seg_id']:<8} {r['n_voxels']:<10} {r['volume_mm3']:<14.3f} {r['struct_name']}"
        )
    return "\n".join(lines)


def _fmt_measures(measures: list[dict]) -> str:
    """Format global volume measures as readable summary lines."""
    lines = []
    for m in measures:
        lines.append(
            f"  {m['name']} ({m['description']}): {m['value']:.3f} {m['unit']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _resolve_case_output_root(arguments: dict, gui_state: dict) -> tuple[str | None, str | None]:
    """Choose the case output directory from explicit args or GUI state."""
    explicit_case_id = str(arguments.get("case_id") or "").strip()
    if explicit_case_id:
        explicit_workspace_id = str(arguments.get("workspace_id") or gui_state.get("current_workspace_id") or gui_state.get("workspace_id") or "").strip()
        if explicit_workspace_id:
            return _case_output_root_from_ids(explicit_workspace_id, explicit_case_id), explicit_case_id
        return None, None

    current_case_id = str(gui_state.get("current_case_id") or "").strip()
    current_workspace_id = str(gui_state.get("current_workspace_id") or gui_state.get("workspace_id") or "").strip()
    if current_case_id and current_workspace_id:
        return _case_output_root_from_ids(current_workspace_id, current_case_id), current_case_id
    return None, None


def handle_read_stats(arguments: dict, gui_state: dict) -> list[ToolTextContent]:
    """Read a case stats file and return optional label-filtered summaries."""
    case_output_root, display_name = _resolve_case_output_root(arguments, gui_state)
    if not case_output_root:
        return [
            ToolTextContent(
                type="text",
                text="Error: no active case. Pass case_id explicitly or load a case first.",
            )
        ]

    stats_file = (arguments.get("stats_file") or "aseg+DKT.stats").strip().lstrip("/")
    # Accept bare filename or full path-like strings — always resolve under stats/
    if os.sep in stats_file or "/" in stats_file:
        # caller passed something like "bert/stats/aseg+DKT.stats" — take the basename
        stats_file = os.path.basename(stats_file)

    path = os.path.join(_OUTPUT_DIR, case_output_root, "stats", stats_file)
    if not os.path.isfile(path):
        return [
            ToolTextContent(
                type="text",
                text=(
                    f"Stats file not found: {path}\n"
                    f"Available files: "
                    + ", ".join(
                        os.listdir(os.path.join(_OUTPUT_DIR, case_output_root, "stats"))
                        if os.path.isdir(os.path.join(_OUTPUT_DIR, case_output_root, "stats"))
                        else []
                    )
                ),
            )
        ]

    try:
        parsed = parse_stats_file(path)
    except Exception as exc:
        return [
            ToolTextContent(type="text", text=f"Error parsing {stats_file}: {exc}")
        ]

    label_query = (arguments.get("label_query") or "").strip()

    # ── No filter: return everything ──────────────────────────────────────
    if not label_query:
        parts = [f"# {stats_file} — case: {display_name or case_output_root}"]
        if parsed["measures"]:
            parts.append("\n## Global measures")
            parts.append(_fmt_measures(parsed["measures"]))
        parts.append(f"\n## Per-structure volumes ({len(parsed['rows'])} structures)")
        parts.append(_fmt_rows(parsed["rows"]))
        return [ToolTextContent(type="text", text="\n".join(parts))]

    # ── Filtered query ────────────────────────────────────────────────────
    result_parts: list[str] = []

    # 1. Try structure rows via LUT soft-matching (supports numeric ID or name)
    label_id, label_name = resolve_label(label_query)
    if label_id is not None:
        matched_rows = [r for r in parsed["rows"] if r["seg_id"] == label_id]
    else:
        # Fall back to substring match on the StructName column in the file
        q = label_query.lower()
        matched_rows = [r for r in parsed["rows"] if q in r["struct_name"].lower()]

    if matched_rows:
        result_parts.append(
            f"## Structure stats for '{label_name or label_query}' in {stats_file} (case: {display_name or case_output_root})"
        )
        result_parts.append(_fmt_rows(matched_rows))

    # 2. Also search global measures by short name, long name, or description
    q = label_query.lower()
    matched_measures = [
        m
        for m in parsed["measures"]
        if q in m["name"].lower()
        or q in m["long_name"].lower()
        or q in m["description"].lower()
    ]
    if matched_measures:
        result_parts.append(f"\n## Global measures matching '{label_query}'")
        result_parts.append(_fmt_measures(matched_measures))

    if not result_parts:
        return [
            ToolTextContent(
                type="text",
                text=f"No structures or measures matching '{label_query}' found in {stats_file}.",
            )
        ]

    return [ToolTextContent(type="text", text="\n".join(result_parts))]
