"""Resolve typed viewer resources into browser-loadable artifact paths."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from api_service.artifacts.service import artifact_download_path_for_output
from backend_common.auth import AuthContext


def _output_path_from_descriptor(command: dict[str, Any], key: str) -> str | None:
    """Return the output descriptor path from a GUI command descriptor."""
    descriptor = command.get(key)
    if isinstance(descriptor, dict) and descriptor.get("kind") == "output":
        path = descriptor.get("path")
        if isinstance(path, str) and path.startswith("outputs/"):
            return path
    return None


def _artifact_path_for_output_resource(
    db: Session,
    context: AuthContext,
    output_descriptor_path: str | None,
) -> str | None:
    """Resolve an output resource descriptor to an artifact download path."""
    if not output_descriptor_path or not output_descriptor_path.startswith("outputs/"):
        return None
    return artifact_download_path_for_output(db, context, output_descriptor_path.removeprefix("outputs/"))


def resolve_load_volume_command(
    db: Session,
    context: AuthContext,
    command: Any,
) -> Any:
    """Attach browser-facing artifact paths to an explicit load-volume command."""
    if not isinstance(command, dict):
        return command
    resolved = dict(command)
    output_path = _output_path_from_descriptor(resolved, "resource")
    download_path = _artifact_path_for_output_resource(db, context, output_path)
    if download_path:
        resolved["download_path"] = download_path

    companion_fields = (
        ("curvature_resource", "curvature_download_path"),
        ("annotation_resource", "annotation_download_path"),
        ("custom_lut_resource", "custom_lut_download_path"),
        ("segmentation_resource", "segmentation_download_path"),
    )
    for resource_key, path_key in companion_fields:
        companion_path = _output_path_from_descriptor(resolved, resource_key)
        companion_download_path = _artifact_path_for_output_resource(db, context, companion_path)
        if companion_download_path:
            resolved[path_key] = companion_download_path
    return resolved


def resolve_gui_resource_descriptors(db: Session, context: AuthContext, payload: dict) -> dict:
    """Resolve typed GUI resource descriptors to app-relative artifact paths."""
    resolved = dict(payload)
    if "requested_load_volume" in resolved:
        resolved["requested_load_volume"] = resolve_load_volume_command(
            db,
            context,
            resolved["requested_load_volume"],
        )
    return resolved
