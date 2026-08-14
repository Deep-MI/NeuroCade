"""Resolve case workflow log paths."""

from __future__ import annotations

from pathlib import Path


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
            processed.append(line)
            continue
        for segment in reversed(line.split("\r")):
            stripped = segment.strip()
            if stripped:
                processed.append(stripped + "\n")
                break
    return "".join(processed[-max_lines:])
