"""Resolve case workflow log paths."""

from __future__ import annotations

import re
from pathlib import Path

# Tool output is terminal output: it carries colour codes, cursor movement, and
# backspaces that a terminal consumes but a browser renders as stray glyphs.
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_SHORT = re.compile(r"\x1b[@-_]")
_RESIDUAL_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def run_log_paths(case_dir: Path, run_id: str) -> tuple[Path, Path]:
    """Return the isolated stdout and stderr paths for one workflow run."""
    normalized = str(run_id).strip()
    if not normalized or Path(normalized).name != normalized or normalized in {".", ".."}:
        raise ValueError("run_id must be a single safe path component")
    run_dir = case_dir / "scripts" / "runs" / normalized
    return run_dir / "stdout.log", run_dir / "stderr.log"


def initialize_run_logs(case_dir: Path, run_id: str) -> tuple[Path, Path]:
    """Create empty isolated logs so a queued run never exposes older output."""
    stdout_path, stderr_path = run_log_paths(case_dir, run_id)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.touch()
    stderr_path.touch()
    return stdout_path, stderr_path


def _read_log_lines(path: Path) -> list[str]:
    """Read a UTF-8 log file without allowing malformed output to fail a request."""
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line + "\n" for line in raw.split("\n") if line]


def _apply_backspaces(value: str) -> str:
    """Resolve backspaces the way a terminal would, by erasing what precedes."""
    if "\b" not in value:
        return value
    rendered: list[str] = []
    for character in value:
        if character == "\b":
            if rendered and rendered[-1] != "\n":
                rendered.pop()
            continue
        rendered.append(character)
    return "".join(rendered)


def _sanitize(value: str) -> str:
    """Render one log line as printable text without terminal control codes."""
    value = _ANSI_OSC.sub("", value)
    value = _ANSI_CSI.sub("", value)
    value = _ANSI_SHORT.sub("", value)
    value = _apply_backspaces(value)
    return _RESIDUAL_CONTROL.sub("", value)


def render_run_logs(case_dir: Path, run_id: str, *, max_lines: int = 1000) -> str:
    """Render one run's stdout and stderr for terminal display."""
    stdout_path, stderr_path = run_log_paths(case_dir, run_id)
    combined = _read_log_lines(stdout_path)
    stderr_lines = _read_log_lines(stderr_path)
    if stderr_lines:
        combined.extend(["--- STDERR ---\n", *stderr_lines])

    processed: list[str] = []
    for line in combined:
        if "WARNING: Found" in line and "files in subject directory" in line:
            continue
        if "Potentially Overwriting:" in line:
            continue
        if "\r" not in line:
            processed.append(_sanitize(line))
            continue
        for segment in reversed(line.split("\r")):
            stripped = _sanitize(segment).strip()
            if stripped:
                processed.append(stripped + "\n")
                break
    return "".join(processed[-max_lines:])


def read_run_log_page(root: Path, run_id: str, *, stream: str = "stdout", offset: int = 0, max_bytes: int = 20000) -> dict:
    """Read one bounded byte page from a fixed run stream within its authorized root."""
    if stream not in {"stdout", "stderr"} or offset < 0 or not 1 <= max_bytes <= 20000:
        raise ValueError("Invalid log pagination")
    paths = run_log_paths(root, run_id)
    path = paths[0 if stream == "stdout" else 1]
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Run log escapes the authorized root")
    try:
        with resolved.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(max_bytes)
            size = handle.seek(0, 2)
    except FileNotFoundError:
        return {
            "stream": stream,
            "text": "",
            "offset": offset,
            "next_offset": offset,
            "size_bytes": 0,
            "available": False,
            "has_more": False,
        }
    return {
        "stream": stream,
        "text": data.decode("utf-8", errors="replace"),
        "offset": offset,
        "next_offset": offset + len(data),
        "size_bytes": size,
        "available": True,
        "has_more": offset + len(data) < size,
    }
