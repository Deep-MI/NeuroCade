"""Helpers for mapping runtime output files to typed resource descriptors."""

from pathlib import PurePosixPath


def output_resource_descriptor(output_descriptor_path: str) -> dict[str, str]:
    """Build a typed descriptor for an output resource."""
    return {"kind": "output", "path": output_descriptor_path.lstrip("/")}


def output_descriptor_path_from_file(file_path: str) -> str:
    """Map a stored output file path to its typed descriptor path."""
    path = PurePosixPath(file_path)
    if path.is_absolute() or not path.parts or path.parts[0] != "workspaces" or ".." in path.parts:
        raise ValueError("Output files must use a workspaces/... path")
    return f"outputs/{path.as_posix()}"
