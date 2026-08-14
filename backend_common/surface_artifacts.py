"""Provide shared backend surface artifacts utilities for NeuroCade."""

SURFACE_FILES = {
    "lh.pial",
    "rh.pial",
    "lh.white",
    "rh.white",
    "lh.inflated",
    "rh.inflated",
    "lh.sphere",
    "rh.sphere",
}

CURVATURE_FILES = {"lh.curv", "rh.curv"}

ANNOTATION_FILES = (
    "lh.aparc.DKTatlas.mapped.annot",
    "rh.aparc.DKTatlas.mapped.annot",
    "lh.aparc.DKTatlas.annot",
    "rh.aparc.DKTatlas.annot",
    "lh.aparc.annot",
    "rh.aparc.annot",
)

def hemisphere_for_filename(filename: str) -> str | None:
    """Extract the left or right hemisphere prefix from a surface filename."""
    hemi = filename.split(".", 1)[0] if "." in filename else None
    return hemi if hemi in {"lh", "rh"} else None


def classify_surface_metadata(filename: str) -> dict:
    """Build viewer metadata for a FreeSurfer surface file."""
    surface_name = filename.split(".", 1)[1] if "." in filename else filename
    return {
        "layer_role": "surface",
        "surface_format": "freesurfer-surf",
        "hemisphere": hemisphere_for_filename(filename),
        "surface_name": surface_name,
    }


def classify_curvature_metadata(filename: str) -> dict:
    """Build viewer metadata for a FreeSurfer curvature file."""
    return {
        "layer_role": "surface-curvature",
        "surface_format": "freesurfer-curv",
        "hemisphere": hemisphere_for_filename(filename),
    }


def classify_annotation_metadata(filename: str) -> dict:
    """Build viewer metadata for a FreeSurfer annotation file."""
    return {
        "layer_role": "surface-annotation",
        "surface_format": "freesurfer-annot",
        "hemisphere": hemisphere_for_filename(filename),
    }
