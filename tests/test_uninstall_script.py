"""Safety contracts for the NeuroCade uninstaller."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_ID = "0123456789abcdef0123456789abcdef"


def _make_installation(tmp_path: Path, *, runtime_owned: bool = False) -> Path:
    root = tmp_path / "NeuroCade"
    (root / "scripts/lib").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/uninstall.sh", root / "scripts/uninstall.sh")
    shutil.copy2(REPO_ROOT / "scripts/lib/env.sh", root / "scripts/lib/env.sh")
    shutil.copy2(REPO_ROOT / "scripts/lib/docker_cli.sh", root / "scripts/lib/docker_cli.sh")
    shutil.copy2(REPO_ROOT / "scripts/lib/processes.sh", root / "scripts/lib/processes.sh")
    (root / "scripts/install.sh").touch()
    (root / "scripts/run.sh").touch()
    runtime = root / ".runtime"
    runtime.mkdir()
    (runtime / "install-id").write_text(f"{INSTALL_ID}\n", encoding="utf-8")
    (runtime / "installed-env").write_text("NEUROCADE_RUNTIME=apptainer\n", encoding="utf-8")
    if runtime_owned:
        (runtime / "runtime-root-owned").touch()
    return root


def _run(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts/uninstall.sh"), *args],
        cwd=root.parent,
        env=env,
        text=True,
        capture_output=True,
    )


def test_uninstaller_refuses_checkout_without_ownership_record(tmp_path: Path) -> None:
    root = _make_installation(tmp_path)
    (root / ".runtime/install-id").unlink()

    result = _run(root, "--yes")

    assert result.returncode == 1
    assert "no NeuroCade ownership record" in result.stderr
    assert root.exists()


def test_uninstaller_preserves_preexisting_runtime_data_and_env(tmp_path: Path) -> None:
    root = _make_installation(tmp_path)
    runtime = root / ".runtime"
    data = root / "existing-data"
    data.mkdir()
    (data / "patient-file").touch()
    (runtime / "unowned-file").touch()
    (runtime / "preinstall-env").write_text("ORIGINAL=value\n", encoding="utf-8")
    (runtime / "electron-dependencies-owned").touch()
    (root / "client/node_modules").mkdir(parents=True)
    installed_env = f"NEUROCADE_RUNTIME=apptainer\nHOST_DATA_DIR={data}\n"
    (runtime / "installed-env").write_text(installed_env, encoding="utf-8")
    (root / ".env").write_text(installed_env, encoding="utf-8")

    result = _run(root, "--yes")

    assert result.returncode == 0, result.stderr
    assert data.exists()
    assert not (root / "client/node_modules").exists()
    assert (runtime / "unowned-file").exists()
    assert not (runtime / "install-id").exists()
    assert (root / ".env").read_text(encoding="utf-8") == "ORIGINAL=value\n"


def test_uninstaller_preserves_configuration_modified_after_install(tmp_path: Path) -> None:
    root = _make_installation(tmp_path)
    runtime = root / ".runtime"
    (runtime / "installed-env").write_text("NEUROCADE_RUNTIME=apptainer\n", encoding="utf-8")
    modified = "NEUROCADE_RUNTIME=apptainer\nUSER_SETTING=keep-me\n"
    (root / ".env").write_text(modified, encoding="utf-8")

    result = _run(root, "--yes")

    assert result.returncode == 0, result.stderr
    assert (root / ".env").read_text(encoding="utf-8") == modified
    assert "Preserving modified configuration" in result.stdout


def test_purge_uses_immutable_installed_paths_not_modified_env(tmp_path: Path) -> None:
    root = _make_installation(tmp_path)
    owned_data = root / "owned-data"
    other_data = root / "other-data"
    for data in (owned_data, other_data):
        data.mkdir()
        (data / ".neurocade-install-id").write_text(f"{INSTALL_ID}\n", encoding="utf-8")
    (root / ".runtime/installed-env").write_text(
        f"NEUROCADE_RUNTIME=apptainer\nHOST_DATA_DIR={owned_data}\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        f"NEUROCADE_RUNTIME=apptainer\nHOST_DATA_DIR={other_data}\n",
        encoding="utf-8",
    )

    result = _run(root, "--yes", "--purge-data")

    assert result.returncode == 0, result.stderr
    assert not owned_data.exists()
    assert other_data.exists()


def test_purge_removes_owned_data_but_preserves_checkout(tmp_path: Path) -> None:
    root = _make_installation(tmp_path, runtime_owned=True)
    runtime = root / ".runtime"
    data = root / "neurocade-data"
    data.mkdir()
    (data / ".neurocade-install-id").write_text(f"{INSTALL_ID}\n", encoding="utf-8")
    (root / ".env").write_text(f"NEUROCADE_RUNTIME=apptainer\nHOST_DATA_DIR={data}\n", encoding="utf-8")

    result = _run(root, "--yes", "--purge-data")

    assert result.returncode == 0, result.stderr
    assert root.exists()
    assert not data.exists()
    assert not runtime.exists()


def test_data_is_preserved_by_default(tmp_path: Path) -> None:
    root = _make_installation(tmp_path, runtime_owned=True)
    runtime = root / ".runtime"
    (runtime / "database").mkdir()
    (runtime / "database/neurocade.db").touch()
    data = root / "neurocade-data"
    data.mkdir()
    (data / ".neurocade-install-id").write_text(f"{INSTALL_ID}\n", encoding="utf-8")
    (root / ".env").write_text(f"NEUROCADE_RUNTIME=apptainer\nHOST_DATA_DIR={data}\n", encoding="utf-8")

    result = _run(root, "--yes")

    assert result.returncode == 0, result.stderr
    assert root.exists()
    assert data.exists()
    assert runtime.exists()
    assert (runtime / "database/neurocade.db").exists()
    assert not (runtime / "install-id").exists()


def test_docker_resources_require_matching_installation_label(tmp_path: Path) -> None:
    root = _make_installation(tmp_path)
    data = root / "existing-data"
    data.mkdir()
    installed_env = f"NEUROCADE_RUNTIME=docker\nHOST_DATA_DIR={data}\nNEUROCADE_IMAGE=example/neurocade:test\n"
    (root / ".runtime/installed-env").write_text(installed_env, encoding="utf-8")
    (root / ".env").write_text(installed_env, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >>'{calls}'\n"
        f"case \"$*\" in *--format*) echo '{INSTALL_ID}' ;; esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = _run(root, "--yes", "--purge-data", "--remove-images", env=env)

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text(encoding="utf-8")
    assert "stop --time 15 neurocade" in recorded
    assert "volume rm neurocade-database" in recorded
    assert "image rm example/neurocade:test" in recorded


def test_uninstaller_preserves_docker_resources_with_other_owner(tmp_path: Path) -> None:
    root = _make_installation(tmp_path)
    installed_env = "NEUROCADE_RUNTIME=docker\nNEUROCADE_IMAGE=example/neurocade:test\n"
    (root / ".runtime/installed-env").write_text(installed_env, encoding="utf-8")
    (root / ".env").write_text(installed_env, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\n' \"$*\" >>'{calls}'\n"
        "case \"$*\" in *--format*) echo 'another-installation' ;; esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = _run(root, "--yes", "--purge-data", "--remove-images", env=env)

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text(encoding="utf-8")
    assert "stop --time" not in recorded
    assert "volume rm" not in recorded
    assert "image rm" not in recorded
