"""Host-side path validation shared by bridge request decoding."""

from pathlib import Path, PurePosixPath

from .protocol import validate_relative_path


def resolve_relative(root: Path, value: str, *, label: str, must_exist: bool) -> Path:
    relative = validate_relative_path(value, label=label, allow_dot=True)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the configured data root") from exc
    if must_exist and not candidate.exists():
        raise ValueError(f"{label} does not exist")
    if candidate.exists() and not (candidate.is_file() or candidate.is_dir()):
        raise ValueError(f"{label} cannot be a device or special file")
    ancestor = candidate if candidate.exists() else next((path for path in candidate.parents if path.exists()), root)
    try:
        ancestor.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the configured data root through a symlink") from exc
    return candidate


def container_path(value: str, *, label: str) -> str:
    path = PurePosixPath(str(value or ""))
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(value):
        raise ValueError(f"{label} must be an absolute normalized path")
    return path.as_posix()
