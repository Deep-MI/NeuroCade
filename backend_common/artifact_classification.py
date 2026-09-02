"""Canonical artifact classification for catalog and filesystem indexing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend_common.db import ArtifactKind
from backend_common.surface_artifacts import (
    ANNOTATION_FILES,
    CURVATURE_FILES,
    SURFACE_FILES,
    classify_annotation_metadata,
    classify_curvature_metadata,
    classify_surface_metadata,
)

DeclaredOutputType = Literal["intensity_volume", "segmentation_volume", "surface", "other"]
VOLUME_SUFFIXES = (".mgz", ".mgh", ".nii", ".nii.gz")


@dataclass(frozen=True)
class ArtifactClassification:
    kind: ArtifactKind
    mime_type: str
    metadata: dict[str, object]


def artifact_mime_type(path: Path) -> str:
    """Infer the stable MIME type persisted for an artifact."""
    lowered = path.name.lower()
    if lowered.endswith(VOLUME_SUFFIXES):
        return "application/octet-stream"
    return {
        ".json": "application/json",
        ".png": "image/png",
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".log": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")


def classify_artifact(
    path: Path,
    *,
    declared_type: DeclaredOutputType | None = None,
    metadata: dict[str, object] | None = None,
    fallback_kind: ArtifactKind | None = None,
) -> ArtifactClassification | None:
    """Classify a declared output or recognizable filesystem artifact."""
    filename = path.name
    values = dict(metadata or {})

    if declared_type == "intensity_volume":
        kind = ArtifactKind.volume
        values["volume_role"] = "intensity"
    elif declared_type == "segmentation_volume":
        kind = ArtifactKind.volume
        values["volume_role"] = "segmentation"
    elif declared_type == "surface":
        kind = ArtifactKind.derived
        values = {**classify_surface_metadata(filename), **values, "layer_role": "surface"}
    elif declared_type == "other":
        kind = ArtifactKind.derived
    elif declared_type is not None:
        raise ValueError(f"Unsupported artifact output type: {declared_type}")
    elif filename in SURFACE_FILES:
        kind = ArtifactKind.derived
        values = {**classify_surface_metadata(filename), **values}
    elif filename in CURVATURE_FILES:
        kind = ArtifactKind.derived
        values = {**classify_curvature_metadata(filename), **values}
    elif filename in ANNOTATION_FILES:
        kind = ArtifactKind.derived
        values = {**classify_annotation_metadata(filename), **values}
    elif filename.lower().endswith(VOLUME_SUFFIXES):
        kind = ArtifactKind.volume
        values.setdefault("volume_role", "intensity")
    elif path.suffix.lower() == ".log":
        kind = ArtifactKind.log
    elif path.suffix.lower() in {".json", ".txt", ".md", ".pdf", ".png"}:
        kind = ArtifactKind.report
    elif fallback_kind is not None:
        kind = fallback_kind
    else:
        return None

    return ArtifactClassification(
        kind=kind,
        mime_type=artifact_mime_type(path),
        metadata=values,
    )
