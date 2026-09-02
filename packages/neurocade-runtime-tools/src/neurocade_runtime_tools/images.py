"""Verified image preparation primitives used only by the native bridge."""

from __future__ import annotations

import codecs
import errno
import hashlib
import json
import os
import pty
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import requests

from .docker_runtime import detected_docker_host_platform
from .execution import ProcessObserver, ProgressObserver, _terminate_process_group, run_managed_command
from .protocol import RuntimeImageSpec

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_ANSI_LINE_MOVEMENT = re.compile(r"\x1b\[[0-9;?]*[ABCDEFGHJKSTf]")
_DOCKER_LAYER_STATUS = re.compile(
    r"^(?:sha256:)?(?P<layer>[0-9a-f]{4,64}):\s+"
    r"(?P<status>Pulling fs layer|Waiting|Downloading|Verifying Checksum|Download complete|"
    r"Extracting|Pull complete|Already exists)(?P<detail>.*)$"
)
_DOCKER_BYTE_PROGRESS = re.compile(
    r"(?P<current>[0-9]+(?:\.[0-9]+)?)\s*(?P<current_unit>[kmgt]?i?b)\s*/\s*"
    r"(?P<total>[0-9]+(?:\.[0-9]+)?)\s*(?P<total_unit>[kmgt]?i?b)",
    re.IGNORECASE,
)
_BYTE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_GIB = 1024**3


class PreparedImage(str):
    """A string-compatible prepared reference carrying Docker's resolved platform."""

    platform: str | None

    def __new__(cls, reference: str, platform: str | None = None) -> PreparedImage:
        value = super().__new__(cls, reference)
        value.platform = platform
        return value

    @property
    def reference(self) -> str:
        return str(self)


def _platform_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    os_name = str(value.get("os") or "").strip().lower()
    architecture = str(value.get("architecture") or "").strip().lower()
    variant = str(value.get("variant") or "").strip().lower()
    if not os_name or not architecture or os_name == "unknown" or architecture == "unknown":
        return None
    return "/".join(part for part in (os_name, architecture, variant) if part)


def _manifest_platforms(payload: object) -> set[str]:
    platforms: set[str] = set()
    if isinstance(payload, list):
        for value in payload:
            platforms.update(_manifest_platforms(value))
        return platforms
    if not isinstance(payload, dict):
        return platforms
    direct = _platform_name(payload.get("platform"))
    descriptor = payload.get("Descriptor")
    descriptor_platform = _platform_name(descriptor.get("platform")) if isinstance(descriptor, dict) else None
    for candidate in (direct, descriptor_platform):
        if candidate is not None:
            platforms.add(candidate)
    manifests = payload.get("manifests")
    if isinstance(manifests, list):
        for manifest in manifests:
            platforms.update(_manifest_platforms(manifest))
    return platforms


def resolve_docker_image_platform(reference: str) -> tuple[str, set[str]]:
    """Prefer the host-native image, with AMD64 as the portable emulation fallback."""
    host = detected_docker_host_platform()
    inspected = run_managed_command(
        ["docker", "manifest", "inspect", "--verbose", reference],
        capture_output=True,
        timeout=60,
    )
    if inspected.returncode != 0:
        detail = (inspected.stderr or inspected.stdout).strip()
        raise RuntimeError(
            f"Could not inspect Docker image platforms for {reference} on detected host {host}"
            + (f": {detail}" if detail else "")
        )
    try:
        supported = _manifest_platforms(json.loads(inspected.stdout))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Docker returned invalid platform metadata for {reference} on detected host {host}"
        ) from exc
    # A variant-specific ARM manifest (for example linux/arm64/v8) satisfies
    # the corresponding native architecture request.
    native = next((value for value in sorted(supported) if value == host or value.startswith(host + "/")), None)
    if native is not None:
        return native, supported
    amd64 = next(
        (value for value in sorted(supported) if value == "linux/amd64" or value.startswith("linux/amd64/")),
        None,
    )
    if amd64 is not None:
        return amd64, supported
    rendered = ", ".join(sorted(supported)) or "none reported"
    raise RuntimeError(
        f"Docker image {reference} cannot run on detected host {host}: "
        f"neither a native image nor the linux/amd64 emulation fallback is available. "
        f"Supported image platforms: {rendered}"
    )


def _storage_preflight() -> dict[str, object]:
    """Return host storage headroom without deleting any Docker data."""
    free = shutil.disk_usage(Path.home()).free
    warning_gib = max(1, int(os.environ.get("NEUROCADE_IMAGE_DISK_WARNING_GIB", "30")))
    minimum_gib = max(1, int(os.environ.get("NEUROCADE_IMAGE_DISK_MINIMUM_GIB", "5")))
    if free < minimum_gib * _GIB:
        raise RuntimeError(
            f"Only {free / _GIB:.1f} GiB of disk space is free; image preparation requires at least "
            f"{minimum_gib} GiB. Free Docker storage and retry."
        )
    warning = (
        f"Only {free / _GIB:.1f} GiB of disk space is free. Large images may fail while extracting."
        if free < warning_gib * _GIB
        else None
    )
    return {
        "disk_free_bytes": free,
        "disk_warning": warning,
        "reclaimable_storage": _docker_reclaimable_summary() if warning else {},
    }


def _docker_reclaimable_summary() -> dict[str, str]:
    """Read Docker's cleanup estimates without pruning any data."""
    try:
        result = run_managed_command(
            ["docker", "system", "df", "--format", "{{json .}}"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    summary: dict[str, str] = {}
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        resource_type = str(row.get("Type") or "").strip()
        reclaimable = str(row.get("Reclaimable") or "").strip()
        if resource_type and reclaimable:
            summary[resource_type] = reclaimable
    return summary


def _byte_value(value: str, unit: str) -> int:
    return int(float(value) * _BYTE_UNITS[unit.lower()])


class _DockerPullProgress:
    """Translate Docker's TTY layer and byte statuses into structured progress."""

    def __init__(
        self,
        image: str,
        observer: ProgressObserver | None,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.image = image
        self.observer = observer
        self.metadata = metadata or {}
        self.layers: set[str] = set()
        self.downloaded: set[str] = set()
        self.extracted: set[str] = set()
        self.download_bytes: dict[str, tuple[int, int]] = {}
        self.extraction_bytes: dict[str, tuple[int, int]] = {}
        self.last_payload: dict[str, object] | None = None
        self.last_activity_publish_at = 0.0

    def publish(
        self,
        *,
        phase: str,
        progress: float | None = None,
        current_bytes: int | None = None,
        total_bytes: int | None = None,
        cached: bool = False,
    ) -> None:
        if self.observer is None:
            return
        payload: dict[str, object] = {
            "kind": "image",
            "image": self.image,
            "phase": phase,
            "completed_layers": len(self.extracted if phase == "extracting" else self.downloaded),
            "total_layers": len(self.layers),
            "cached": cached,
            **self.metadata,
        }
        if progress is not None:
            payload["progress"] = max(0.0, min(1.0, progress))
        if current_bytes is not None:
            payload["current_bytes"] = current_bytes
        if total_bytes is not None:
            payload["total_bytes"] = total_bytes
        self.last_payload = payload
        self.last_activity_publish_at = time.monotonic()
        self.observer(payload)

    def note_output(self) -> None:
        """Keep active pulls live even when Docker emits an unknown progress format."""
        if self.observer is None or time.monotonic() - self.last_activity_publish_at < 2.0:
            return
        payload = dict(self.last_payload or {
            "kind": "image",
            "image": self.image,
            "phase": "downloading",
            "completed_layers": len(self.downloaded),
            "total_layers": len(self.layers),
            "cached": False,
            **self.metadata,
        })
        # An initial zero is only a placeholder. Once Docker is producing output,
        # omit it unless byte progress has actually been parsed.
        if not self.download_bytes and payload.get("progress") == 0.0:
            payload.pop("progress", None)
        self.last_payload = payload
        self.last_activity_publish_at = time.monotonic()
        self.observer(payload)

    def feed(self, line: str) -> bool:
        cleaned = _ANSI_ESCAPE.sub("", line).strip()
        match = _DOCKER_LAYER_STATUS.match(cleaned)
        if match is None:
            return False
        layer = match.group("layer")
        status = match.group("status")
        self.layers.add(layer)
        byte_progress = _DOCKER_BYTE_PROGRESS.search(match.group("detail"))
        if byte_progress is not None:
            current = _byte_value(byte_progress.group("current"), byte_progress.group("current_unit"))
            total = _byte_value(byte_progress.group("total"), byte_progress.group("total_unit"))
            target = self.extraction_bytes if status == "Extracting" else self.download_bytes
            target[layer] = (min(current, total), total)
        if status in {"Download complete", "Pull complete", "Already exists"}:
            self.downloaded.add(layer)
        if status in {"Pull complete", "Already exists"}:
            self.extracted.add(layer)
        if status == "Extracting":
            current_bytes = sum(current for current, _total in self.extraction_bytes.values())
            total_bytes = sum(total for _current, total in self.extraction_bytes.values())
            self.publish(
                phase="extracting",
                progress=current_bytes / total_bytes if total_bytes else None,
                current_bytes=current_bytes or None,
                total_bytes=total_bytes or None,
            )
            return True
        if status == "Downloading" and self.download_bytes:
            current_bytes = sum(current for current, _total in self.download_bytes.values())
            total_bytes = sum(total for _current, total in self.download_bytes.values())
            self.publish(
                phase="downloading",
                progress=current_bytes / total_bytes if total_bytes else None,
                current_bytes=current_bytes,
                total_bytes=total_bytes or None,
            )
            return True
        if status == "Pull complete":
            self.publish(phase="preparing")
        return True


def _progress_frames(value: str) -> tuple[list[str], str]:
    # Docker redraws a multi-line TTY display with cursor movement instead of
    # consistently terminating every layer row. Treat those movements as frame
    # boundaries before stripping the remaining ANSI decoration in feed().
    normalized = _ANSI_LINE_MOVEMENT.sub("\n", value).replace("\r", "\n")
    parts = normalized.split("\n")
    return parts[:-1], parts[-1]


def _pull_docker_image(
    reference: str,
    *,
    platform: str | None,
    process_observer: ProcessObserver | None,
    progress_observer: ProgressObserver | None,
    metadata: dict[str, object] | None = None,
) -> None:
    tracker = _DockerPullProgress(reference, progress_observer, metadata=metadata)
    tracker.publish(phase="downloading", progress=0.0)
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    output: list[str] = []
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    try:
        command = ["docker", "pull"]
        if platform is not None:
            command.extend(["--platform", platform])
        command.append(reference)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        if process_observer is not None:
            process_observer(cast(subprocess.Popen[str], process))
        while True:
            try:
                chunk = os.read(master_fd, 8192)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            text = decoder.decode(chunk)
            output.append(text)
            frames, pending = _progress_frames(pending + text)
            for frame in frames:
                tracker.feed(frame)
            tracker.note_output()
        pending += decoder.decode(b"", final=True)
        if pending:
            tracker.feed(pending)
        returncode = process.wait()
        if returncode != 0:
            diagnostic = _ANSI_ESCAPE.sub("", "".join(output)).replace("\r", "\n")
            lines = [line.strip() for line in diagnostic.splitlines() if line.strip()]
            detail = "\n".join(lines[-20:])
            message = f"Docker image pull failed for {reference}"
            if detail:
                message += f":\n{detail}"
            raise RuntimeError(message)
        tracker.publish(phase="verifying")
        tracker.publish(phase="ready", progress=1.0)
    except BaseException:
        if process is not None:
            _terminate_process_group(process)
        raise
    finally:
        os.close(master_fd)
        if slave_fd >= 0:
            os.close(slave_fd)
        if process_observer is not None:
            process_observer(None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def sif_cache_path(spec: RuntimeImageSpec, image_dir: Path) -> Path:
    stem = spec.oci_reference.replace("/", "_").replace(":", "_")
    return image_dir / f"{stem}.sif"


def download_verified_file(
    url: str,
    target: Path,
    *,
    expected_sha256: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress_observer: ProgressObserver | None = None,
    label: str = "Download",
) -> Path:
    """Download an HTTPS resource atomically and optionally verify its SHA-256."""
    if target.is_file() and (expected_sha256 is None or sha256_file(target) == expected_sha256):
        return target
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{label} URL must use HTTPS")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    partial.unlink(missing_ok=True)
    try:
        with requests.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            current = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(4 * 1024 * 1024):
                    if is_cancelled is not None and is_cancelled():
                        raise InterruptedError(f"{label} was canceled")
                    if chunk:
                        output.write(chunk)
                        current += len(chunk)
                        if progress_observer is not None:
                            progress_observer({
                                "kind": "image",
                                "phase": "downloading",
                                "progress": current / total if total else None,
                                "current_bytes": current,
                                "total_bytes": total or None,
                            })
        if expected_sha256 is not None:
            actual = sha256_file(partial)
            if actual != expected_sha256:
                raise RuntimeError(f"{label} checksum mismatch: expected {expected_sha256}, got {actual}")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def prepare_image(
    spec: RuntimeImageSpec,
    *,
    backend: str,
    image_dir: Path,
    process_observer: ProcessObserver | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress_observer: ProgressObserver | None = None,
) -> PreparedImage:
    """Prepare an immutable runtime image, serializing concurrent cache misses."""
    if backend == "docker":
        host_platform = detected_docker_host_platform()
        tracker = _DockerPullProgress(spec.oci_reference, progress_observer, metadata={"host_platform": host_platform})
        tracker.publish(phase="waiting")
        with _image_lock(f"docker:{spec.docker_reference}"):
            if progress_observer is not None:
                progress_observer({
                    "kind": "image",
                    "image": spec.oci_reference,
                    "phase": "resolving_platform",
                    "host_platform": host_platform,
                })
            resolved_platform, supported_platforms = resolve_docker_image_platform(spec.docker_reference)
            metadata: dict[str, object] = {
                "host_platform": host_platform,
                "platform": resolved_platform,
                "supported_platforms": sorted(supported_platforms),
            }
            present = run_managed_command(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Os}}/{{.Architecture}}{{if .Variant}}/{{.Variant}}{{end}}",
                    spec.docker_reference,
                ],
                capture_output=True,
            )
            if present.returncode != 0 or present.stdout.strip() != resolved_platform:
                preflight = {**_storage_preflight(), **metadata}
                _pull_docker_image(
                    spec.docker_reference,
                    platform=resolved_platform,
                    process_observer=process_observer,
                    progress_observer=progress_observer,
                    metadata=preflight,
                )
            else:
                _DockerPullProgress(spec.oci_reference, progress_observer, metadata=metadata).publish(
                    phase="ready", progress=1.0, cached=True
                )
        return PreparedImage(spec.docker_reference, resolved_platform)
    if backend != "apptainer":
        raise ValueError(f"Unsupported runtime backend: {backend}")

    def publish_apptainer(payload: dict[str, object]) -> None:
        if progress_observer is not None:
            progress_observer({"kind": "image", "image": spec.oci_reference, **payload})

    publish_apptainer({"phase": "waiting"})
    image_dir.mkdir(parents=True, exist_ok=True)
    target = sif_cache_path(spec, image_dir)
    with _image_lock(str(target)):
        accepted = {checksum for checksum in (spec.sif_sha256, spec.converted_sif_sha256) if checksum}
        if target.is_file() and (not accepted or sha256_file(target) in accepted):
            publish_apptainer({"phase": "ready", "progress": 1.0, "cached": True})
            return PreparedImage(str(target))
        target.unlink(missing_ok=True)
        if spec.sif_url:
            assert spec.sif_sha256 is not None
            download_verified_file(
                spec.sif_url,
                target,
                expected_sha256=spec.sif_sha256,
                is_cancelled=is_cancelled,
                progress_observer=publish_apptainer,
                label="SIF download",
            )
        else:
            partial = target.with_suffix(".sif.partial")
            partial.unlink(missing_ok=True)
            try:
                run_managed_command(
                    ["apptainer", "pull", "--force", str(partial), f"docker://{spec.apptainer_reference}"],
                    check=True,
                    process_observer=process_observer,
                )
                if spec.converted_sif_sha256:
                    actual = sha256_file(partial)
                    if actual != spec.converted_sif_sha256:
                        raise RuntimeError(
                            f"Converted SIF checksum mismatch: expected {spec.converted_sif_sha256}, got {actual}"
                        )
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
        publish_apptainer({"phase": "ready", "progress": 1.0, "cached": False})
    return PreparedImage(str(target))


def load_image_manifest(path: Path) -> list[RuntimeImageSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("images", payload) if isinstance(payload, dict) else payload
    result = []
    for row in rows:
        result.append(
            RuntimeImageSpec(
                oci_reference=row.get("oci_reference") or row["image"],
                oci_digest=row.get("oci_digest"),
                sif_url=row.get("sif_url") or row.get("direct_url"),
                sif_sha256=row.get("sif_sha256") or row.get("direct_sha256"),
                converted_sif_sha256=row.get("converted_sif_sha256") or row.get("sha256"),
            )
        )
    return result
