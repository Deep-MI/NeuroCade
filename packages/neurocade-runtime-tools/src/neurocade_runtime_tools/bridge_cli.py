"""Command-line interface for the native runtime bridge."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from .bridge_server import serve_bridge
from .execution import run_managed_command
from .images import download_verified_file, load_image_manifest, prepare_image


def _format_bytes(value: object) -> str | None:
    if not isinstance(value, int) or value < 0:
        return None
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    return None


class _CliImageProgress:
    """Render image preparation progress without flooding captured install logs."""

    def __init__(self, stream: TextIO = sys.stderr) -> None:
        self.stream = stream
        self.is_tty = stream.isatty()
        self._last_phase: dict[str, str] = {}
        self._last_bucket: dict[str, int] = {}

    def __call__(self, payload: Mapping[str, object]) -> None:
        image = str(payload.get("image") or "runtime image")
        phase = str(payload.get("phase") or "preparing")
        progress = payload.get("progress")
        bucket = int(float(progress) * 20) if isinstance(progress, (int, float)) else -1
        changed = self._last_phase.get(image) != phase or self._last_bucket.get(image) != bucket
        if not self.is_tty and not changed:
            return

        details: list[str] = []
        current = _format_bytes(payload.get("current_bytes"))
        total = _format_bytes(payload.get("total_bytes"))
        if current and total:
            details.append(f"{current} / {total}")
        elif current:
            details.append(current)
        completed = payload.get("completed_layers")
        layer_total = payload.get("total_layers")
        if isinstance(completed, int) and isinstance(layer_total, int) and layer_total > 0:
            details.append(f"{completed}/{layer_total} layers")
        if isinstance(progress, (int, float)):
            details.append(f"{max(0, min(100, round(float(progress) * 100)))}%")
        if payload.get("cached") is True:
            details.append("cached")
        suffix = f" ({', '.join(details)})" if details else ""
        message = f"{phase.capitalize()} {image}{suffix}"

        if self.is_tty:
            end = "\n" if phase == "ready" else ""
            print(f"\r\033[2K{message}", end=end, file=self.stream, flush=True)
        else:
            print(message, file=self.stream, flush=True)
        self._last_phase[image] = phase
        self._last_bucket[image] = bucket


def _daemonize(*, pid_file: Path, log_file: Path) -> bool:
    """Detach from the launcher and return whether this is the daemon process."""
    pid_file.unlink(missing_ok=True)
    first_child = os.fork()
    if first_child:
        _, status = os.waitpid(first_child, 0)
        if status != 0:
            raise RuntimeError("Runtime bridge daemonization failed")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                daemon_pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(daemon_pid, 0)
                return False
            except (FileNotFoundError, ProcessLookupError, ValueError):
                time.sleep(0.01)
        raise RuntimeError("Runtime bridge daemon did not publish a live PID")

    os.setsid()
    second_child = os.fork()
    if second_child:
        os._exit(0)

    os.umask(0o077)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stdin_fd = os.open(os.devnull, os.O_RDONLY)
    log_fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(stdin_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(stdin_fd)
    if log_fd > 2:
        os.close(log_fd)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return True


def _remove_owned_pid_file(pid_file: Path) -> None:
    try:
        if int(pid_file.read_text(encoding="utf-8").strip()) == os.getpid():
            pid_file.unlink(missing_ok=True)
    except (FileNotFoundError, ValueError):
        pass


def serve(args: argparse.Namespace) -> int:
    if not args.launch_id.strip():
        raise ValueError("--launch-id must not be empty")
    pid_file = Path(args.pid_file) if args.pid_file else None
    if args.daemonize:
        if pid_file is None or not args.log_file:
            raise ValueError("--daemonize requires --pid-file and --log-file")
        if not _daemonize(pid_file=pid_file, log_file=Path(args.log_file)):
            return 0
    try:
        return serve_bridge(
            backend=args.runtime,
            data_root=Path(args.data_root),
            image_dir=Path(args.image_dir),
            host=args.host,
            port=args.port,
            token_file=Path(args.token_file),
            launch_id=args.launch_id,
        )
    finally:
        if args.daemonize and pid_file is not None:
            _remove_owned_pid_file(pid_file)


def doctor(args: argparse.Namespace) -> int:
    failures = 0
    executable = "docker" if args.runtime == "docker" else "apptainer"
    try:
        run_managed_command([executable, "--version"], check=True, capture_output=True)
        print(f"OK: {executable} is available")
    except (OSError, subprocess.SubprocessError) as exc:
        failures += 1
        print(f"ERROR: {executable} is unavailable: {exc}")
    try:
        root = Path(args.data_root).resolve(strict=True)
        if not os.access(root, os.W_OK):
            raise RuntimeError("data root is not writable")
        print(f"OK: data root is writable: {root}")
    except (OSError, RuntimeError) as exc:
        failures += 1
        print(f"ERROR: {exc}")
    return 1 if failures else 0


def prepare_images_command(args: argparse.Namespace) -> int:
    progress = _CliImageProgress()
    for spec in load_image_manifest(Path(args.manifest)):
        print(
            prepare_image(
                spec,
                backend=args.runtime,
                image_dir=Path(args.image_dir),
                progress_observer=progress,
            )
        )
    return 0


def download_verified(args: argparse.Namespace) -> int:
    download_verified_file(args.url, Path(args.target), expected_sha256=args.sha256)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neurocade-runtime-bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runtime", choices=("docker", "apptainer"), required=True)
    common.add_argument("--data-root", required=True)
    common.add_argument("--image-dir", required=True)
    serve_parser = subparsers.add_parser("serve", parents=[common])
    serve_parser.add_argument("--host", required=True)
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--token-file", required=True)
    serve_parser.add_argument("--launch-id", required=True)
    serve_parser.add_argument("--daemonize", action="store_true")
    serve_parser.add_argument("--pid-file")
    serve_parser.add_argument("--log-file")
    serve_parser.set_defaults(func=serve)
    doctor_parser = subparsers.add_parser("doctor", parents=[common])
    doctor_parser.set_defaults(func=doctor)
    images_parser = subparsers.add_parser("prepare-images", parents=[common])
    images_parser.add_argument("--manifest", required=True)
    images_parser.set_defaults(func=prepare_images_command)
    download_parser = subparsers.add_parser("download-verified")
    download_parser.add_argument("--url", required=True)
    download_parser.add_argument("--sha256")
    download_parser.add_argument("--target", required=True)
    download_parser.set_defaults(func=download_verified)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
