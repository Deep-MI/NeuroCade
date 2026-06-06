"""Helpers for mapping runtime output files to typed resource descriptors."""

def output_descriptor_path(*parts: str) -> str:
    """Build a normalized descriptor path under the outputs namespace."""
    normalized = "/".join(part.strip("/") for part in parts if part)
    return f"outputs/{normalized}"


def output_resource_descriptor(output_descriptor_path: str) -> dict[str, str]:
    """Build a typed descriptor for an output resource."""
    return {"kind": "output", "path": output_descriptor_path.lstrip("/")}


def output_descriptor_path_from_file(file_path: str) -> str:
    """Map a stored output file path to its typed descriptor path."""
    normalized = file_path.lstrip("/")
    if normalized.startswith("output/"):
        normalized = normalized[len("output/") :]
    if normalized.startswith("data/"):
        normalized = normalized[len("data/") :]
    return output_descriptor_path(normalized)
