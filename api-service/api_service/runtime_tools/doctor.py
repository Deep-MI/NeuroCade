"""Validate the runtime environment inside the NeuroCade application container."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from neurocade_runtime_tools.apptainer_runtime import (
    apptainer_sif_path,
    configured_gpu_mode,
    nvidia_capability,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from api_service.runtime_tools.prepare_images import _verified, image_manifest
from backend_common.settings import get_settings


def _report(level: str, message: str) -> None:
    print(f"{level:<5} {message}", file=sys.stderr if level in {"FAIL", "WARN"} else sys.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-download", action="store_true", help="Skip checks for images that have not been downloaded yet.")
    args = parser.parse_args()
    settings = get_settings()
    failures = 0

    if shutil.which("apptainer"):
        _report("OK", "Apptainer is installed")
    else:
        _report("FAIL", "Apptainer is not installed")
        failures += 1
    unsquash = os.environ.get("APPTAINER_UNSQUASH", "").strip().lower() in {"1", "true", "yes", "on"}
    if unsquash:
        tmpdir = Path(os.environ.get("APPTAINER_TMPDIR", "/tmp"))
        try:
            probe = tmpdir / ".doctor-apptainer-tmp-probe"
            probe.touch()
            probe.unlink()
            _report("OK", f"Apptainer extraction workspace {tmpdir} is writable")
        except OSError as exc:
            _report("FAIL", f"Apptainer extraction workspace {tmpdir} is not writable: {exc}")
            failures += 1
    elif Path("/dev/fuse").is_char_device():
        _report("OK", "/dev/fuse is a character device inside the application container")
    else:
        _report("FAIL", "/dev/fuse is not a character device inside the application container")
        failures += 1

    for path in (settings.fs_data_root, settings.outputs_dir, settings.sif_dir):
        if path.is_dir():
            try:
                probe = path / ".doctor-write-probe"
                probe.touch()
                probe.unlink()
                _report("OK", f"{path} is writable")
            except OSError as exc:
                _report("FAIL", f"{path} is not writable: {exc}")
                failures += 1
        else:
            _report("FAIL", f"{path} does not exist")
            failures += 1

    database_url = make_url(settings.sqlalchemy_database_url)
    if database_url.get_backend_name() == "sqlite" and database_url.database:
        database_dir = Path(database_url.database).parent
        try:
            probe = database_dir / ".doctor-database-write-probe"
            probe.touch()
            probe.unlink()
            _report("OK", f"SQLite directory {database_dir} is writable")
        except OSError as exc:
            _report("FAIL", f"SQLite directory {database_dir} is not writable: {exc}")
            failures += 1
        try:
            engine = create_engine(settings.sqlalchemy_database_url)
            with engine.connect() as connection:
                current_revision = MigrationContext.configure(connection).get_current_revision()
            script = ScriptDirectory.from_config(Config(str(Path(__file__).parents[3] / "config" / "alembic.ini")))
            if current_revision is None:
                _report("OK", "SQLite schema will be initialized on first startup")
            else:
                script.get_revision(current_revision)
                _report("OK", f"SQLite schema revision {current_revision} is recognized")
        except CommandError:
            _report(
                "FAIL",
                f"SQLite schema revision {current_revision} is incompatible with this release; reset application state before startup",
            )
            failures += 1

    if not args.pre_download:
        for spec in image_manifest():
            target = apptainer_sif_path(spec.image, sif_dir=settings.sif_dir)
            if _verified(target, spec):
                _report("OK", f"{spec.id} image checksum is valid")
            else:
                _report("FAIL", f"{spec.id} image is missing or failed checksum validation")
                failures += 1

    mode = configured_gpu_mode()
    capability = nvidia_capability()
    if capability.available:
        _report("OK", f"GPU is available: {capability.reason}")
    elif mode == "cuda":
        _report("FAIL", f"CUDA is required but unavailable: {capability.reason}")
        failures += 1
    else:
        _report("WARN", f"GPU is unavailable; workflows will use CPU: {capability.reason}")

    if failures:
        _report("FAIL", f"Container checks found {failures} fatal problem(s)")
        return 1
    _report("OK", "Container runtime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
