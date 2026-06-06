"""Compute the RAS-canonical voxel centroid of a segmentation label.

Used by the ``gui_focus_label`` handler to move the viewer crosshairs to
a named anatomical structure.
"""

from __future__ import annotations

import os
from typing import Any, cast

import numpy as np
import nibabel as nib

from .lut import resolve_label


def _ras_reorientation(affine: np.ndarray):
    """Determine the axis permutation and flips to go from file storage
    order to canonical RAS orientation — mirrors the JavaScript VolumeLoader's
    ``orientationFromMatrix`` exactly.

    Returns ``(axis_order, flips)`` where:
    - ``axis_order[w]`` is the file-storage axis that maps to RAS axis *w*
    - ``flips[w]`` is True if that axis must be reversed
    """
    # Extract the 3×3 rotation/scale part (rows = RAS axes, columns = voxel axes)
    m = affine[:3, :3]

    order = [0, 0, 0]
    flips = [False, False, False]
    used: set[int] = set()

    for w in range(3):          # w = RAS axis (0=R, 1=A, 2=S)
        best = -1
        best_abs = -1.0
        best_val = 0.0
        for v in range(3):      # v = file-storage axis
            if v in used:
                continue
            mag = abs(m[w, v])
            if mag > best_abs:
                best_abs = mag
                best_val = float(m[w, v])
                best = v
        order[w] = best
        flips[w] = best_val < 0
        used.add(best)

    return order, flips


def get_label_centroid(segmentation_path: str, label_query: str, lut_path: str | None = None) -> dict:
    """
    Finds the centroid coordinate (x, y, z) for a given label in a segmentation mask.
    label_query can be an integer ID or a string name (supports fuzzy matching).

    The returned (x, y, z) are **RAS-canonical voxel indices** — i.e. the same
    coordinate space the web viewer uses after its ``reorient()`` pass.
    """
    if not os.path.exists(segmentation_path):
        return {"error": f"Segmentation file not found: {segmentation_path}"}

    # Resolve the label via the shared LUT module
    label_id, label_name = resolve_label(label_query, lut_path)
    if label_id is None:
        return {"error": f"Label '{label_query}' not found in FreeSurferColorLUT."}

    # Load the segmentation mask
    try:
        nib_module = cast(Any, nib)
        img = nib_module.load(segmentation_path)
        data = img.get_fdata()
        raw_dims = data.shape          # file-storage dimensions

        # Find all voxels matching the label
        coords = np.argwhere(data == label_id)

        if len(coords) == 0:
             return {"error": f"Label {label_id} ({label_name}) not found in the segmentation volume."}

        # Calculate the centroid in raw file-storage voxel space
        centroid_raw = coords.mean(axis=0)   # [i, j, k] in file order

        # Convert to RAS-canonical voxel coordinates (matching the viewer).
        # The viewer's reorient() maps:
        #   ras_x = file_axis[order[0]], flipped if flips[0]
        #   ras_y = file_axis[order[1]], flipped if flips[1]
        #   ras_z = file_axis[order[2]], flipped if flips[2]
        affine = img.affine
        order, flips = _ras_reorientation(affine)

        ras_centroid = [0.0, 0.0, 0.0]
        for w in range(3):
            v = order[w]                       # which file axis maps to RAS axis w
            if flips[w]:
                ras_centroid[w] = raw_dims[v] - 1 - centroid_raw[v]
            else:
                ras_centroid[w] = centroid_raw[v]

        return {
            "success": True,
            "label_id": label_id,
            "label_name": label_name,
            "x": int(round(ras_centroid[0])),
            "y": int(round(ras_centroid[1])),
            "z": int(round(ras_centroid[2]))
        }
    except Exception as e:
         return {"error": f"Error processing volume: {str(e)}"}
