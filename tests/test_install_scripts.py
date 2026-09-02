"""Behavioral contracts for the matched host runtime launchers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_managed_python_helper(tmp_path: Path, command: str, *, path: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [
            "bash",
            "-c",
            'ROOT_DIR="$1"; source "$2"; ' + command,
            "managed-python-test",
            str(tmp_path),
            str(REPO_ROOT / "scripts/lib/managed_python.sh"),
        ],
        text=True,
        capture_output=True,
        env=env,
    )


def test_managed_uv_ignores_conflicting_path_installation(tmp_path: Path) -> None:
    local_bin = tmp_path / ".runtime/uv-bin"
    path_bin = tmp_path / "path-bin"
    local_bin.mkdir(parents=True)
    path_bin.mkdir()
    calls = tmp_path / "calls"
    (local_bin / "uv").write_text(
        f'#!/usr/bin/env bash\nprintf "local %s\\n" "$*" >>"{calls}"\n'
        'if [[ "$1" == "--version" ]]; then echo "uv 0.8.17"; fi\n',
        encoding="utf-8",
    )
    (path_bin / "uv").write_text(f'#!/usr/bin/env bash\nprintf "global %s\\n" "$*" >>"{calls}"\n', encoding="utf-8")
    (path_bin / "file").write_text(
        '#!/usr/bin/env bash\nprintf "%s: Mach-O 64-bit executable arm64\\n" "$1"\n',
        encoding="utf-8",
    )
    for executable in (local_bin / "uv", path_bin / "uv", path_bin / "file"):
        executable.chmod(0o755)

    result = _run_managed_python_helper(
        tmp_path,
        "managed_python_path",
        path=f"{path_bin}{os.pathsep}{os.environ['PATH']}",
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "local --version",
        "local python find --managed-python 3.12",
    ]


def test_host_arch_detects_apple_silicon_through_rosetta(tmp_path: Path) -> None:
    path_bin = tmp_path / "bin"
    path_bin.mkdir()
    (path_bin / "uname").write_text(
        '#!/usr/bin/env bash\nif [[ "$1" == "-s" ]]; then echo Darwin; else echo x86_64; fi\n',
        encoding="utf-8",
    )
    (path_bin / "sysctl").write_text('#!/usr/bin/env bash\necho 1\n', encoding="utf-8")
    for executable in (path_bin / "uname", path_bin / "sysctl"):
        executable.chmod(0o755)

    result = _run_managed_python_helper(
        tmp_path,
        'neurocade_host_arch; neurocade_is_rosetta && echo translated',
        path=f"{path_bin}{os.pathsep}{os.environ['PATH']}",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["arm64", "translated"]


def _write_runtime_probe(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _select_default_runtime(
    tmp_path: Path,
    *,
    os_name: str,
    apptainer_works: bool,
    docker_works: bool = True,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    namespace_file = tmp_path / "max_user_namespaces"
    namespace_file.write_text("1024\n", encoding="utf-8")
    _write_runtime_probe(bin_dir, "uname", f'[[ "$1" == "-s" ]] && echo {os_name} || echo x86_64')
    _write_runtime_probe(bin_dir, "id", '[[ "$1" == "-u" ]] && echo 1000')
    if docker_works:
        _write_runtime_probe(bin_dir, "docker", "exit 0")
    _write_runtime_probe(bin_dir, "apptainer", 'echo "--no-home"' if apptainer_works else "exit 1")
    env = os.environ.copy()
    fallback_path = env["PATH"] if docker_works else f"/usr/bin{os.pathsep}/bin"
    env["PATH"] = f"{bin_dir}{os.pathsep}{fallback_path}"
    return subprocess.run(
        [
            "bash",
            "-c",
            'USER_NAMESPACE_FILE="$1"; source "$2"; default_runtime',
            "runtime-selection-test",
            str(namespace_file),
            str(REPO_ROOT / "scripts/lib/runtime_selection.sh"),
        ],
        text=True,
        capture_output=True,
        env=env,
    )


def test_default_runtime_selection_matrix(tmp_path: Path) -> None:
    cases = [
        ("macos", "Darwin", True, True, "docker"),
        ("linux-apptainer", "Linux", True, True, "apptainer"),
        ("linux-docker", "Linux", False, True, "docker"),
        ("apptainer-only", "Linux", True, False, "apptainer"),
    ]
    for name, os_name, apptainer_works, docker_works, expected in cases:
        result = _select_default_runtime(
            tmp_path / name,
            os_name=os_name,
            apptainer_works=apptainer_works,
            docker_works=docker_works,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


def _render_driver_command(tmp_path: Path, driver: str, builder: str) -> list[str]:
    app_url = tmp_path / "app-url"
    app_url.write_text("http://localhost:8000\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "IMAGE": "example/app:1",
            "DOCKER_PLATFORM": "linux/amd64",
            "CONTAINER_NAME": "neurocade-test",
            "HOST_DATA_DIR": str(data),
            "DATABASE_VOLUME": "neurocade-test-database",
            "APPTAINER_DATABASE_DIR": str(tmp_path / "database"),
            "BRIDGE_TOKEN_FILE": str(tmp_path / "token"),
            "HTTP_BIND": "127.0.0.1",
            "HTTP_PORT": "8000",
            "ENV_FILE": str(tmp_path / ".env"),
            "BRIDGE_PORT": "8765",
            "APP_URL_FILE": str(app_url),
            "SAMPLE_CASE_DIR": str(tmp_path / "missing-sample"),
            "APP_SIF": str(tmp_path / "app.sif"),
        }
    )
    array_name = "DOCKER_APP_ARGS" if driver == "runtime_docker.sh" else "APPTAINER_APP_COMMAND"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "$1"; {builder}; printf "%s\\n" "${{{array_name}[@]}}"',
            "driver-test",
            str(REPO_ROOT / "scripts/lib" / driver),
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    return result.stdout.splitlines()


def test_docker_driver_builds_only_docker_application_command(tmp_path: Path) -> None:
    argv = _render_driver_command(tmp_path, "runtime_docker.sh", "docker_run_args")
    assert argv[:2] == ["docker", "run"]
    assert "host.docker.internal:host-gateway" in argv
    assert "apptainer" not in argv


def test_apptainer_driver_builds_only_rootless_application_command(tmp_path: Path) -> None:
    argv = _render_driver_command(tmp_path, "runtime_apptainer.sh", "build_apptainer_application_command")
    assert argv[:5] == ["apptainer", "exec", "--cleanenv", "--no-home", "--containall"]
    assert "--fakeroot" not in argv
    assert "docker" not in argv


def test_launcher_rejects_missing_runtime_before_mutation(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HOST_DATA_DIR=./data\n", encoding="utf-8")
    env = os.environ.copy()
    env["ENV_FILE"] = str(env_file)
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/run.sh"), "status"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "NEUROCADE_RUNTIME=docker|apptainer is required" in result.stderr


def test_release_manifest_round_trip(tmp_path: Path) -> None:
    manifest = tmp_path / "neurocade-release.json"
    script = REPO_ROOT / "scripts/release/release_manifest.py"
    subprocess.run(
        [
            str(script),
            "create",
            "--tag",
            "v2026.8.30",
            "--version",
            "2026.8.30",
            "--sif",
            "neurocade-app-2026.8.30-amd64.sif",
            "--bridge",
            "neurocade_runtime_tools-0.2.0-py3-none-any.whl",
            "--output",
            str(manifest),
        ],
        check=True,
    )
    result = subprocess.run([str(script), "read", str(manifest)], check=True, text=True, capture_output=True)
    assert result.stdout.splitlines() == [
        "v2026.8.30",
        "2026.8.30",
        "neurocade-app-2026.8.30-amd64.sif",
        "neurocade-app-2026.8.30-amd64.sif.sha256",
        "neurocade_runtime_tools-0.2.0-py3-none-any.whl",
        "neurocade_runtime_tools-0.2.0-py3-none-any.whl.sha256",
    ]


def test_release_artifact_installer_downloads_and_verifies_assets(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    release_scripts = checkout / "scripts/release"
    release_scripts.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/release/release_manifest.py", release_scripts)
    assets = tmp_path / "assets"
    assets.mkdir()
    sif_name = "neurocade-app-2026.8.30-amd64.sif"
    bridge_name = "neurocade_runtime_tools-0.2.0-py3-none-any.whl"
    (assets / sif_name).write_bytes(b"test-sif")
    (assets / bridge_name).write_bytes(b"test-wheel")
    for name in (sif_name, bridge_name):
        digest = subprocess.check_output(["sha256sum", str(assets / name)], text=True).split()[0]
        (assets / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(release_scripts / "release_manifest.py"),
            "create",
            "--tag",
            "v2026.8.30",
            "--version",
            "2026.8.30",
            "--sif",
            sif_name,
            "--bridge",
            bridge_name,
            "--output",
            str(assets / "neurocade-release.json"),
        ],
        check=True,
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        "for ((i=1; i<=$#; i++)); do\n"
        "  [[ \"${!i}\" == -o ]] && { j=$((i+1)); target=\"${!j}\"; }\n"
        "  [[ \"${!i}\" == *://* ]] && url=\"${!i}\"\n"
        "done\n"
        'exec /bin/cp "$FAKE_ASSET_DIR/${url##*/}" "$target"\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = os.environ.copy()
    env.update({"FAKE_ASSET_DIR": str(assets), "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}"})
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; install_latest_apptainer_release "$2" "$3"; '
            'printf "%s\\n%s\\n" "$NEUROCADE_RESOLVED_BRIDGE_PACKAGE" "$NEUROCADE_RESOLVED_RELEASE_VERSION"',
            "release-artifact-test",
            str(REPO_ROOT / "scripts/lib/apptainer_artifacts.sh"),
            str(checkout),
            sys.executable,
        ],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (checkout / ".runtime/images/neurocade-app-amd64.sif").read_bytes() == b"test-sif"
    assert result.stdout.splitlines()[-2:] == [
        str(checkout / ".runtime/release" / bridge_name),
        "2026.8.30",
    ]
