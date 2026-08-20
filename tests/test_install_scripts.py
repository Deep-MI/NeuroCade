"""Static and lightweight launcher tests for matched host runtime profiles."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_shell_entrypoints_parse() -> None:
    for name in (
        "scripts/install.sh",
        "scripts/run.sh",
        "scripts/desktop/run.sh",
        "scripts/admin/reset_app_state.sh",
        "scripts/release/compute_tag.sh",
        "scripts/release/wait_for_http.sh",
    ):
        entrypoint = REPO_ROOT / name
        subprocess.run(["bash", "-n", str(entrypoint)], check=True)
        assert os.access(entrypoint, os.X_OK), f"Shell entrypoint is not executable: {name}"


def test_installer_selects_or_accepts_a_matched_runtime() -> None:
    text = (REPO_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    assert "--runtime docker|apptainer" in text
    assert 'RUNTIME="$(default_runtime)"' in text
    assert 'configured_or_default "$ROOT_DIR" NEUROCADE_RUNTIME' in text
    assert 'validate_runtime "$RUNTIME"' in text
    assert 'managed_uv python install "$NEUROCADE_PYTHON_VERSION"' in text
    assert "bridge-token" in text
    assert "NEUROCADE_SIF_DIR" not in text


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
        'if [[ "$1" == "--version" ]]; then echo "uv 0.8.17 (10960bc13 2025-09-10)"; fi\n',
        encoding="utf-8",
    )
    (path_bin / "uv").write_text(
        f'#!/usr/bin/env bash\nprintf "global %s\\n" "$*" >>"{calls}"\n',
        encoding="utf-8",
    )
    (local_bin / "uv").chmod(0o755)
    (path_bin / "uv").chmod(0o755)

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


def test_managed_python_directory_is_repository_local(tmp_path: Path) -> None:
    result = _run_managed_python_helper(tmp_path, 'printf "%s\\n" "$UV_PYTHON_INSTALL_DIR"')

    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path / ".runtime/python")


def test_host_arch_detects_apple_silicon_through_rosetta(tmp_path: Path) -> None:
    path_bin = tmp_path / "bin"
    path_bin.mkdir()
    (path_bin / "uname").write_text(
        '#!/usr/bin/env bash\nif [[ "$1" == "-s" ]]; then echo Darwin; else echo x86_64; fi\n',
        encoding="utf-8",
    )
    (path_bin / "sysctl").write_text('#!/usr/bin/env bash\necho 1\n', encoding="utf-8")
    (path_bin / "uname").chmod(0o755)
    (path_bin / "sysctl").chmod(0o755)

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


def _select_default_runtime(tmp_path: Path, *, os_name: str, apptainer_works: bool) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    namespace_file = tmp_path / "max_user_namespaces"
    namespace_file.write_text("1024\n", encoding="utf-8")
    _write_runtime_probe(bin_dir, "uname", f'[[ "$1" == "-s" ]] && echo {os_name} || echo x86_64')
    _write_runtime_probe(bin_dir, "id", '[[ "$1" == "-u" ]] && echo 1000')
    _write_runtime_probe(bin_dir, "docker", "exit 0")
    apptainer_body = 'echo "--no-home"' if apptainer_works else "exit 1"
    _write_runtime_probe(bin_dir, "apptainer", apptainer_body)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
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


def test_default_runtime_is_docker_on_macos(tmp_path: Path) -> None:
    result = _select_default_runtime(tmp_path, os_name="Darwin", apptainer_works=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "docker"


def test_default_runtime_prefers_rootless_apptainer_on_linux(tmp_path: Path) -> None:
    result = _select_default_runtime(tmp_path, os_name="Linux", apptainer_works=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apptainer"


def test_default_runtime_falls_back_to_docker_on_linux(tmp_path: Path) -> None:
    result = _select_default_runtime(tmp_path, os_name="Linux", apptainer_works=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "docker"


def test_launcher_uses_managed_toolchain_and_reports_architecture() -> None:
    install_text = (REPO_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    run_text = (REPO_ROOT / "scripts/run.sh").read_text(encoding="utf-8")
    helper_text = (REPO_ROOT / "scripts/lib/managed_python.sh").read_text(encoding="utf-8")

    assert "command -v uv" not in install_text + run_text
    assert 'source "$ROOT_DIR/scripts/lib/managed_python.sh"' in install_text
    assert 'source "$ROOT_DIR/scripts/lib/managed_python.sh"' in run_text
    assert "UV_NO_MODIFY_PATH=1" in helper_text
    assert "report_managed_toolchain" in run_text
    assert "neurocade_host_arch" in install_text
    assert 'managed_uv venv --clear --python "$python_bin"' in run_text


def test_launcher_has_common_bridge_lifecycle() -> None:
    text = (REPO_ROOT / "scripts/run.sh").read_text(encoding="utf-8")
    for command in ("start", "stop", "status", "logs", "pull", "build", "prepare-tools", "doctor"):
        assert command in text
    assert "neurocade-runtime-bridge" in text
    assert "chmod 600" in text
    assert "start_bridge" in text and "stop_bridge" in text
    assert "--daemonize --pid-file" in text
    assert "prepare_tools\n    start_bridge" in text
    assert "acquire_launcher_lock" in text
    assert 'port_in_use "$BRIDGE_PORT" && fail' in text
    assert "The existing runtime bridge process is not healthy" in text


def test_docker_profile_has_no_nested_runtime_or_privilege() -> None:
    run_text = (REPO_ROOT / "scripts/run.sh").read_text(encoding="utf-8")
    driver_text = (REPO_ROOT / "scripts/lib/runtime_docker.sh").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "docker/backend.Dockerfile").read_text(encoding="utf-8")
    assert "--privileged" not in run_text
    assert "/dev/fuse" not in run_text
    assert "APPTAINER_" not in dockerfile
    assert "apptainer.deb" not in dockerfile
    assert "host.docker.internal:host-gateway" in driver_text
    assert '"$BRIDGE_TOKEN_FILE:/run/neurocade/bridge-token:ro"' in driver_text
    assert '"$DATABASE_VOLUME:/database"' in driver_text
    assert 'docker volume create "$DATABASE_VOLUME"' in driver_text


def test_apptainer_profile_is_rootless_and_does_not_call_docker_driver() -> None:
    driver_text = (REPO_ROOT / "scripts/lib/runtime_apptainer.sh").read_text(encoding="utf-8")
    assert "apptainer exec --cleanenv --no-home --containall" in driver_text
    assert "--fakeroot" not in driver_text
    assert "sudo" not in driver_text
    assert "docker run" not in driver_text


def _render_driver_command(tmp_path: Path, driver: str, builder: str) -> list[str]:
    app_url = tmp_path / "app-url"
    app_url.write_text("http://localhost:8000\n", encoding="utf-8")
    data = tmp_path / "data"
    sample = tmp_path / "missing-sample"
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
            "SAMPLE_CASE_DIR": str(sample),
            "APP_SIF": str(tmp_path / "app.sif"),
        }
    )
    array_name = "DOCKER_APP_ARGS" if driver == "runtime_docker.sh" else "APPTAINER_APP_COMMAND"
    shell_command = f'source "$1"; {builder}; printf "%s\\n" "${{{array_name}[@]}}"'
    result = subprocess.run(
        [
            "bash",
            "-c",
            shell_command,
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


def test_docker_stop_uses_only_the_selected_runtime_driver(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    shutil.copytree(REPO_ROOT / "scripts/lib", scripts / "lib")
    shutil.copy2(REPO_ROOT / "scripts/run.sh", scripts / "run.sh")
    data_root = tmp_path / "data"
    env_file = checkout / ".env"
    env_file.write_text(
        f"NEUROCADE_RUNTIME=docker\nHOST_DATA_DIR={data_root}\nNEUROCADE_CONTAINER_NAME=test-app\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime_log = tmp_path / "runtime.log"
    docker = bin_dir / "docker"
    docker.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >>"$FAKE_RUNTIME_LOG"\n', encoding="utf-8")
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "FAKE_RUNTIME_LOG": str(runtime_log),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    subprocess.run(["bash", str(scripts / "run.sh"), "stop"], check=True, env=env, cwd=checkout)

    calls = runtime_log.read_text(encoding="utf-8").splitlines()
    assert calls == ["stop --time 15 test-app", "rm test-app"]


def test_release_publishes_and_smoke_tests_application_sif() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Convert exact release image to application SIF" in workflow
    assert "Smoke-test application SIF as non-root" in workflow
    assert workflow.count("scripts/release/wait_for_http.sh") == 3
    assert "sha256sum \"$app_sif\"" in workflow
    assert 'gh release create "${args[@]}" "$APP_SIF" "$APP_SIF.sha256"' in workflow


def test_env_example_documents_new_runtime_contract() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NEUROCADE_RUNTIME=docker" in text
    assert "NEUROCADE_BRIDGE_URL=" in text
    assert "NEUROCADE_BRIDGE_TOKEN_FILE=" in text
    assert "NEUROCADE_APP_SIF_URL=" in text
    assert "NEUROCADE_SIF_DIR=" not in text
