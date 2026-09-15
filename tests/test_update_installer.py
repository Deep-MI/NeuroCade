"""Updater provenance, source ownership, and rollback contracts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UPDATE = REPO_ROOT / "scripts/update.sh"
SOURCE_TOOL = REPO_ROOT / "scripts/update_source.py"


def _release(tmp_path: Path, *, install_fails: bool = False) -> tuple[Path, Path]:
    source = tmp_path / "release/NeuroCade-2.0.0"
    (source / "scripts").mkdir(parents=True)
    shutil.copy2(SOURCE_TOOL, source / "scripts/update_source.py")
    (source / "scripts/update.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (source / "scripts/run.sh").write_text(
        '#!/usr/bin/env bash\ncase "$1" in active-runs) echo 0;; stop) echo new-stop >>"$TEST_LOG";; start) echo new-start >>"$TEST_LOG";; esac\n',
        encoding="utf-8",
    )
    (source / "scripts/install.sh").write_text(
        '#!/usr/bin/env bash\necho install-new >>"$TEST_LOG"\n' + ("exit 17\n" if install_fails else "exit 0\n"),
        encoding="utf-8",
    )
    (source / "version.txt").write_text("new\n", encoding="utf-8")
    for script in (source / "scripts").iterdir():
        script.chmod(0o755)
    archive = tmp_path / "neurocade-source-2.0.0.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=source.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    manifest = tmp_path / "neurocade-release.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tag": "v2.0.0",
                "version": "2.0.0",
                "architecture": "amd64",
                "source_revision": "a" * 40,
                "minimum_updater_version": 1,
                "source_archive": {"filename": archive.name, "sha256_filename": f"{archive.name}.sha256"},
                "application_sif": {"filename": "app.sif", "sha256_filename": "app.sif.sha256"},
                "runtime_bridge": {"filename": "bridge.whl", "sha256_filename": "bridge.whl.sha256"},
            }
        ),
        encoding="utf-8",
    )
    return archive, manifest


def _installation(tmp_path: Path) -> tuple[Path, Path]:
    install = tmp_path / "installed"
    (install / "scripts/lib").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/lib/env.sh", install / "scripts/lib/env.sh")
    (install / ".runtime/database").mkdir(parents=True)
    (install / ".runtime/images").mkdir()
    (install / ".runtime/release").mkdir()
    (install / ".runtime/runtime-root-owned").touch()
    (install / "scripts/run.sh").write_text(
        '#!/usr/bin/env bash\ncase "$1" in active-runs) echo 0;; stop) echo old-stop >>"$TEST_LOG";; start) echo old-start >>"$TEST_LOG";; esac\n',
        encoding="utf-8",
    )
    (install / "scripts/install.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (install / "version.txt").write_text("old\n", encoding="utf-8")
    for script in (install / "scripts").iterdir():
        script.chmod(0o755)
    (install / ".env").write_text(
        "NEUROCADE_RUNTIME=apptainer\nDEPLOYMENT_PROFILE=local\nLLM_PROVIDER_DEFAULT=no-llm\n",
        encoding="utf-8",
    )
    subprocess.run([str(SOURCE_TOOL), "write-manifest", str(install), str(install / ".runtime/source-manifest.json")], check=True)
    log = tmp_path / "actions.log"
    return install, log


def test_source_transaction_detects_edits_and_rolls_back(tmp_path: Path) -> None:
    install, _ = _installation(tmp_path)
    manifest = install / ".runtime/source-manifest.json"
    (install / "version.txt").write_text("edited\n", encoding="utf-8")
    result = subprocess.run([str(SOURCE_TOOL), "check", str(install), str(manifest)], text=True, capture_output=True)
    assert result.returncode == 3
    assert "version.txt" in result.stdout


@pytest.mark.parametrize("install_fails", [False, True])
def test_update_switches_source_or_rolls_back_on_install_failure(tmp_path: Path, install_fails: bool) -> None:
    install, log = _installation(tmp_path)
    archive, manifest = _release(tmp_path, install_fails=install_fails)
    env = os.environ.copy()
    env.update({"NEUROCADE_INSTALL_DIR": str(install), "TEST_LOG": str(log)})
    result = subprocess.run(
        [str(UPDATE), "--bootstrap-staged", str(archive), str(manifest), "--yes"],
        env=env,
        text=True,
        capture_output=True,
    )
    if install_fails:
        assert result.returncode != 0
        assert (install / "version.txt").read_text() == "old\n"
        assert "old-start" in log.read_text()
        assert "restoring NeuroCade" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert (install / "version.txt").read_text() == "new\n"
        assert "Updated NeuroCade legacy -> 2.0.0." in result.stdout


def test_update_refuses_bad_source_checksum_before_stopping(tmp_path: Path) -> None:
    install, log = _installation(tmp_path)
    archive, manifest = _release(tmp_path)
    (tmp_path / f"{archive.name}.sha256").write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({"NEUROCADE_INSTALL_DIR": str(install), "TEST_LOG": str(log)})
    result = subprocess.run(
        [str(UPDATE), "--bootstrap-staged", str(archive), str(manifest), "--yes"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "Checksum verification failed" in result.stderr
    assert not log.exists()


def test_update_refuses_to_stop_while_workflow_is_active(tmp_path: Path) -> None:
    install, log = _installation(tmp_path)
    archive, manifest = _release(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python3"
    python.write_text(
        f'#!/usr/bin/env bash\nif [[ "$1" == - ]]; then echo 2; exit 0; fi\nexec "{shutil.which("python3")}" "$@"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (install / ".runtime/bridge.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (install / ".runtime/bridge-token").write_text("test-token\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "NEUROCADE_INSTALL_DIR": str(install),
            "TEST_LOG": str(log),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(UPDATE), "--bootstrap-staged", str(archive), str(manifest), "--yes"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "2 workflow(s) are still active" in result.stderr
    assert not log.exists()


def test_remote_installer_routes_owned_archive_through_verified_updater(tmp_path: Path) -> None:
    install = tmp_path / "installed"
    (install / "scripts").mkdir(parents=True)
    (install / "scripts/install.sh").write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    (install / ".runtime").mkdir()
    (install / ".runtime/runtime-root-owned").touch()

    source = tmp_path / "source/NeuroCade-2.0.0"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts/update.sh").write_text(
        '#!/usr/bin/env bash\nprintf "verified-update:%s:%s\\n" "$NEUROCADE_INSTALL_DIR" "$1"\n',
        encoding="utf-8",
    )
    archive = tmp_path / "assets/neurocade-source-2.0.0.tar.gz"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=source.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (archive.parent / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "tag": "v2.0.0",
        "source_archive": {"filename": archive.name, "sha256_filename": f"{archive.name}.sha256"},
    }
    (archive.parent / "neurocade-release.json").write_text(json.dumps(manifest), encoding="utf-8")

    bootstrap = tmp_path / "bootstrap/install.sh"
    bootstrap.parent.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/install.sh", bootstrap)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/usr/bin/env bash\nfor ((i=1;i<=$#;i++)); do [[ "${!i}" == -o ]] && { j=$((i+1)); out="${!j}"; }; [[ "${!i}" == http* ]] && url="${!i}"; done\ncp "$ASSETS/${url##*/}" "$out"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "NEUROCADE_INSTALL_DIR": str(install),
            "ASSETS": str(archive.parent),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(["bash", str(bootstrap), "--yes"], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert f"verified-update:{install}:--bootstrap-staged" in result.stdout
