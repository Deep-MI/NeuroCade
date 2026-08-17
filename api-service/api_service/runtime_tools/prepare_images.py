"""Prepare the pinned Apptainer images required by a default installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests
from neurocade_runtime_tools.apptainer_runtime import (
    SIF_DIR_ENV,
    RuntimeGpuUnavailableError,
    apptainer_sif_path,
    resolve_gpu_enabled,
)
from tqdm import tqdm

from backend_common.settings import ROOT_DIR

MANIFEST_PATH = ROOT_DIR / "config" / "tool_images.json"
DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
LOG_INTERVAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class ImageSpec:
    id: str
    image: str
    sha256: str
    size_bytes: int
    gpu: bool
    direct_url: str | None = None
    direct_sha256: str | None = None

    @property
    def accepted_checksums(self) -> set[str]:
        return {value for value in (self.sha256, self.direct_sha256) if value}


def _architecture() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)


def image_manifest(path: Path = MANIFEST_PATH) -> list[ImageSpec]:
    """Load and validate the release-pinned default image manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_arch = str(payload.get("architecture") or "")
    if expected_arch != _architecture():
        raise RuntimeError(f"Tool images require {expected_arch}; this host is {_architecture()}")
    images = [ImageSpec(**row) for row in payload.get("images", [])]
    if not images or len({item.id for item in images}) != len(images):
        raise RuntimeError("Tool image manifest must contain unique default images")
    for item in images:
        if len(item.sha256) != 64 or (item.direct_sha256 and len(item.direct_sha256) != 64):
            raise RuntimeError(f"Invalid SHA-256 in tool image manifest for {item.id}")
    return images


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, spec: ImageSpec) -> bool:
    return path.is_file() and path.stat().st_size > 0 and _sha256(path) in spec.accepted_checksums


def _download_direct(spec: ImageSpec, target: Path, *, position: int) -> None:
    if not spec.direct_url or not spec.direct_sha256:
        raise RuntimeError("no direct image is available")
    partial = target.with_name(f".{target.name}.download.partial")
    offset = partial.stat().st_size if partial.is_file() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with requests.get(spec.direct_url, headers=headers, stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        append = offset > 0 and response.status_code == 206
        if not append:
            offset = 0
        total = offset + int(response.headers.get("content-length") or max(spec.size_bytes - offset, 0))
        interactive = sys.stderr.isatty()
        progress = tqdm(
            total=total,
            initial=offset,
            desc=spec.id,
            unit="B",
            unit_scale=True,
            position=position,
            leave=True,
            disable=not interactive,
        )
        downloaded = offset
        last_log = time.monotonic()
        try:
            with partial.open("ab" if append else "wb") as output:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    if not chunk:
                        continue
                    output.write(chunk)
                    downloaded += len(chunk)
                    progress.update(len(chunk))
                    now = time.monotonic()
                    if not interactive and now - last_log >= LOG_INTERVAL_SECONDS:
                        percent = 100 * downloaded / total if total else 0
                        print(f"Downloading {spec.id}: {downloaded}/{total} bytes ({percent:.0f}%)", flush=True)
                        last_log = now
        finally:
            progress.close()
    actual = _sha256(partial)
    if actual != spec.direct_sha256:
        raise RuntimeError(f"checksum mismatch (expected {spec.direct_sha256}, got {actual})")
    partial.replace(target)


def _pull_with_apptainer(spec: ImageSpec, target: Path) -> None:
    partial = target.with_name(f".{target.name}.pull.partial")
    partial.unlink(missing_ok=True)
    try:
        subprocess.run(
            ["apptainer", "pull", "--force", str(partial), f"docker://{spec.image}"],
            check=True,
        )
        actual = _sha256(partial)
        if actual != spec.sha256:
            raise RuntimeError(f"checksum mismatch (expected {spec.sha256}, got {actual})")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def prepare_image(spec: ImageSpec, *, force: bool = False, position: int = 0) -> Path:
    """Prepare one pinned image, preferring a resumable direct NeuroDesk download."""
    sif_dir = os.environ.get(SIF_DIR_ENV)
    if not sif_dir:
        raise RuntimeError(f"{SIF_DIR_ENV} must be configured")
    target = apptainer_sif_path(spec.image, sif_dir=sif_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not force and _verified(target, spec):
        print(f"Using verified tool image: {target.name}", flush=True)
        return target
    if target.exists():
        target.unlink()

    if spec.direct_url:
        try:
            _download_direct(spec, target, position=position)
            return target
        except (OSError, RuntimeError, requests.RequestException) as exc:
            print(f"Direct download failed for {spec.id}: {exc}; falling back to apptainer pull.", file=sys.stderr)
    _pull_with_apptainer(spec, target)
    return target


def prepare_images(*, force: bool = False) -> list[Path]:
    """Prepare default images concurrently and fail after all workers settle."""
    specs = image_manifest()
    interactive = sys.stderr.isatty()
    overall = tqdm(total=len(specs), desc="images", unit="image", position=len(specs), disable=not interactive)
    prepared: dict[str, Path] = {}
    try:
        with ThreadPoolExecutor(max_workers=len(specs), thread_name_prefix="tool-image") as executor:
            futures = {
                executor.submit(prepare_image, spec, force=force, position=index): spec
                for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                spec = futures[future]
                prepared[spec.id] = future.result()
                overall.update(1)
                if not interactive:
                    print(f"Prepared {spec.id} ({len(prepared)}/{len(specs)} images).", flush=True)
    finally:
        overall.close()
    return [prepared[spec.id] for spec in specs]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Redownload images that already pass validation.")
    args = parser.parse_args()
    try:
        paths = prepare_images(force=args.force)
        for spec, path in zip(image_manifest(), paths, strict=True):
            if spec.gpu and not resolve_gpu_enabled(True, image=str(path)):
                print(f"Tool image will use CPU in this deployment: {spec.image}")
    except (OSError, RuntimeError, subprocess.SubprocessError, RuntimeGpuUnavailableError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared {len(paths)} verified tool images.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
