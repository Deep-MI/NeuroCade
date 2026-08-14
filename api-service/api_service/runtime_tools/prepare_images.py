"""Prepare persistent Apptainer images before clinical workflows start."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from neurocade_runtime_tools.runtime_backends import (
    SIF_DIR_ENV,
    RuntimeGpuUnavailableError,
    apptainer_sif_path,
    resolve_gpu_enabled,
)

from api_service.runtime_tools.neurodesk_images import ensure_image_prepared
from api_service.runtime_tools.workflow_catalog import workflows


def workflow_images() -> list[str]:
    """Return unique images required by the workflow catalog."""
    return sorted({tool.neurodesk_image for tool in workflows()})


def prepare_image(image: str, *, force: bool = False) -> Path:
    """Prepare one image, using NeuroDesk's direct SIF when cataloged."""
    sif_dir = os.environ.get(SIF_DIR_ENV)
    if not sif_dir:
        raise RuntimeError(f"{SIF_DIR_ENV} must be configured")
    target = apptainer_sif_path(image, sif_dir=sif_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0 and not force:
        print(f"Using cached tool image: {target.name}")
        return target

    prepared = ensure_image_prepared(image, force=force)
    if prepared is not None:
        return prepared

    temporary = target.with_name(f".{target.stem}.partial.sif")
    temporary.unlink(missing_ok=True)
    print(f"Preparing tool image {image} -> {target.name}")
    try:
        subprocess.run(
            ["apptainer", "--quiet", "pull", "--force", str(temporary), f"docker://{image}"],
            check=True,
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild SIF files that already exist.")
    args = parser.parse_args()
    images = workflow_images()
    try:
        for image in images:
            prepare_image(image, force=args.force)
            if not resolve_gpu_enabled(True, image=image):
                print(f"Tool image will use CPU in this deployment: {image}")
    except RuntimeGpuUnavailableError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared {len(images)} persistent tool image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
