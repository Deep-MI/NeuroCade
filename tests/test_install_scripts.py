"""Behavioral contracts for the matched host runtime launchers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _shell_command(*parts: str) -> str:
    return " ".join(parts)


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


def test_existing_app_check_handles_missing_app_url_cleanly(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            _shell_command(
                'APP_URL_FILE="$1/missing-app-url"; BRIDGE_VENV="$1/missing-venv";',
                'MCP_ENABLED=false; MCP_ACCESS=standard; source "$2"; mcp_check_existing',
            ),
            "mcp-existing-test",
            str(tmp_path),
            str(REPO_ROOT / "scripts/lib/mcp.sh"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "this install has no recorded app URL" in result.stderr
    assert "Traceback" not in result.stderr


def test_local_docker_image_is_scoped_to_installation() -> None:
    install_script = (REPO_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; local_docker_image 0123456789abcdef0123456789abcdef',
            "local-image-test",
            str(REPO_ROOT / "scripts/install.sh"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "neurocade:local-0123456789ab\n"
    assert 'IMAGE_OVERRIDE="$(local_docker_image "$INSTALL_ID")"' in install_script
    assert "configured_image=" not in install_script


def test_apptainer_stop_matches_rewritten_runtime_process(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            _shell_command(
                'APP_PID_FILE="$1/app.pid"; APP_SIF="$1/images/neurocade-app-amd64.sif";',
                'ROOT_DIR="$1"; stop_pid_file() { printf "%s\\n%s\\n" "$1" "$2"; };',
                'source "$2"; runtime_stop_application',
            ),
            "apptainer-stop-test",
            str(tmp_path),
            str(REPO_ROOT / "scripts/lib/runtime_apptainer.sh"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(tmp_path / "app.pid"), "neurocade-app-amd64.sif"]


def test_process_identity_checks_start_time_and_stops_match(tmp_path: Path) -> None:
    pid_file = tmp_path / "app.pid"
    pid_file.write_text("123\n456\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            _shell_command(
                'set -e; REAL_UID="$(id -u)"; CURRENT_START=456; STOPPED=0;',
                'source "$1";',
                'id() { echo "$REAL_UID"; };',
                'ps() { if [[ "$*" == *"uid="* ]]; then echo "$REAL_UID"; else echo "Apptainer runtime parent: neurocade-app-amd64.sif"; fi; };',
                'pid_start_time() { printf "%s\\n" "$CURRENT_START"; };',
                'kill() { if [[ "$1" == "-0" ]]; then [[ "$STOPPED" -eq 0 ]]; else STOPPED=1; echo "$1 $2"; fi; };',
                'pid_matches "$2" neurocade-app-amd64.sif;',
                'CURRENT_START=999; if pid_matches "$2" neurocade-app-amd64.sif; then exit 9; fi;',
                'CURRENT_START=456; stop_pid_file "$2" neurocade-app-amd64.sif',
            ),
            "process-identity-test",
            str(REPO_ROOT / "scripts/lib/processes.sh"),
            str(pid_file),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "-TERM 123" in result.stdout
    assert not pid_file.exists()


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


def _bootstrap_installer(
    tmp_path: Path,
    *,
    install_dir: Path | None,
) -> subprocess.CompletedProcess[str]:
    bootstrap_dir = tmp_path / "bootstrap"
    bootstrap_dir.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/install.sh", bootstrap_dir / "install.sh")
    archive_root = tmp_path / "archive/NeuroCade-main"
    (archive_root / "scripts").mkdir(parents=True)
    installed_script = archive_root / "scripts/install.sh"
    installed_script.write_text(
        '#!/usr/bin/env bash\nroot="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
        'printf "installed:%s\\n" "$root"\n',
        encoding="utf-8",
    )
    installed_script.chmod(0o755)
    archive = tmp_path / "neurocade.tar.gz"
    subprocess.run(["tar", "-czf", str(archive), "-C", str(archive_root.parent), archive_root.name], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text('#!/usr/bin/env bash\nexec /bin/cat "$FAKE_ARCHIVE"\n', encoding="utf-8")
    fake_curl.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "FAKE_ARCHIVE": str(archive),
            "NEUROCADE_ARCHIVE_URL": "https://example.invalid/neurocade.tar.gz",
            "HOME": str(tmp_path / "home"),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )
    if install_dir is not None:
        env["NEUROCADE_INSTALL_DIR"] = str(install_dir)
    else:
        env.pop("NEUROCADE_INSTALL_DIR", None)
    return subprocess.run(
        ["bash", str(bootstrap_dir / "install.sh"), "--yes"],
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        env=env,
    )


def test_noninteractive_bootstrap_reports_default_install_directory(tmp_path: Path) -> None:
    result = _bootstrap_installer(tmp_path, install_dir=None)
    expected = tmp_path / "home/NeuroCade"

    assert result.returncode == 0, result.stderr
    assert f"Installing NeuroCade to {expected}" in result.stdout
    assert f"installed:{expected}" in result.stdout
    assert (expected / "scripts/install.sh").is_file()


def test_bootstrap_accepts_existing_empty_install_directory(tmp_path: Path) -> None:
    install_dir = tmp_path / "existing-empty"
    install_dir.mkdir()
    result = _bootstrap_installer(tmp_path, install_dir=install_dir)

    assert result.returncode == 0, result.stderr
    assert f"installed:{install_dir}" in result.stdout
    assert (install_dir / "scripts/install.sh").is_file()
    assert not (install_dir / "NeuroCade-main").exists()


def test_noninteractive_prompt_uses_default_without_reading_stdin() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; ASSUME_YES=0; prompt "Provider" "no-llm"',
            "prompt-test",
            str(REPO_ROOT / "scripts/install.sh"),
        ],
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "no-llm\n"


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
    env_file = tmp_path / ".env"
    env_file.write_text('LOCAL_AUTH_NAME="Local User"\n', encoding="utf-8")
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
            "ENV_FILE": str(env_file),
            "DOCKER_ENV_FILE": str(tmp_path / "docker.env"),
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
            f'source "$1"; source "$2"; {builder}; printf "%s\\n" "${{{array_name}[@]}}"',
            "driver-test",
            str(REPO_ROOT / "scripts/lib/env.sh"),
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
    assert str(tmp_path / "docker.env") in argv
    assert (tmp_path / "docker.env").read_text(encoding="utf-8") == "LOCAL_AUTH_NAME=Local User\n"


def test_apptainer_driver_builds_only_rootless_application_command(tmp_path: Path) -> None:
    argv = _render_driver_command(tmp_path, "runtime_apptainer.sh", "build_apptainer_application_command")
    assert argv[:5] == ["apptainer", "exec", "--cleanenv", "--no-home", "--containall"]
    assert "--fakeroot" not in argv
    assert "docker" not in argv
    assert str(tmp_path / ".env") in argv


def test_docker_database_rejects_volume_owned_by_another_install(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            _shell_command(
                "set -e; DATABASE_VOLUME=neurocade-database; INSTALL_ID=current-install;",
                'docker() { if [[ "$*" == *--format* ]]; then echo other-install; fi; };',
                'fail() { echo "ERROR: $*" >&2; return 1; }; source "$1"; runtime_prepare_database',
            ),
            "docker-volume-owner-test",
            str(REPO_ROOT / "scripts/lib/runtime_docker.sh"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "belongs to another NeuroCade installation" in result.stderr


def test_docker_database_warns_when_reusing_unowned_volume(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            _shell_command(
                "set -e; DATABASE_VOLUME=neurocade-database; INSTALL_ID=current-install;",
                'IMAGE=neurocade:test; DOCKER_PLATFORM="";',
                'docker() { if [[ "$*" == *--format* ]]; then echo "<no value>"; fi; };',
                'fail() { echo "ERROR: $*" >&2; return 1; };',
                'source "$1"; runtime_prepare_database',
            ),
            "docker-volume-unowned-test",
            str(REPO_ROOT / "scripts/lib/runtime_docker.sh"),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Reusing existing unowned Docker database volume" in result.stderr


def test_installer_env_serialization_round_trips_shell_characters(tmp_path: Path) -> None:
    value = 'Local User "quoted" $HOME `command` \\ path'
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; line="$(env_line LOCAL_AUTH_NAME "$2")"; '
            'encoded="${line#*=}"; decoded="$(decode_env_value "$encoded")"; '
            'printf "%s\\n%s\\n" "$line" "$decoded"',
            "env-round-trip-test",
            str(REPO_ROOT / "scripts/lib/env.sh"),
            value,
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout.splitlines() == [
        'LOCAL_AUTH_NAME="Local User \\"quoted\\" \\$HOME \\`command\\` \\\\ path"',
        value,
    ]
    env_file = tmp_path / ".env"
    env_file.write_text(result.stdout.splitlines()[0] + "\n", encoding="utf-8")
    shell_result = subprocess.run(
        ["bash", "-c", 'set -a; source "$1"; printf "%s" "$LOCAL_AUTH_NAME"', "env-shell-test", str(env_file)],
        check=True,
        text=True,
        capture_output=True,
    )
    assert shell_result.stdout == value


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
            "--source",
            "neurocade-source-2026.8.30.tar.gz",
            "--source-revision",
            "a" * 40,
            "--output",
            str(manifest),
        ],
        check=True,
    )
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema_version"] == 1
    result = subprocess.run([str(script), "read", str(manifest)], check=True, text=True, capture_output=True)
    assert result.stdout.splitlines() == [
        "v2026.8.30",
        "2026.8.30",
        "neurocade-app-2026.8.30-amd64.sif",
        "neurocade-app-2026.8.30-amd64.sif.sha256",
        "neurocade_runtime_tools-0.2.0-py3-none-any.whl",
        "neurocade_runtime_tools-0.2.0-py3-none-any.whl.sha256",
    ]
    update = subprocess.run([str(script), "read-update", str(manifest)], check=True, text=True, capture_output=True)
    assert update.stdout.splitlines() == [
        "v2026.8.30", "2026.8.30", "a" * 40, "1",
        "neurocade-source-2026.8.30.tar.gz", "neurocade-source-2026.8.30.tar.gz.sha256",
    ]


def test_release_resolver_selects_only_compatible_channel_assets(tmp_path: Path) -> None:
    releases = tmp_path / "releases.json"
    releases.write_text(
        json.dumps(
            [
                {
                    "tag_name": "v2026.9.10",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": "legacy-client.tar.gz"}],
                },
                {
                    "tag_name": "v2026.9.9-beta.1",
                    "draft": False,
                    "prerelease": True,
                    "assets": [{"name": "neurocade-release.json"}],
                },
                {
                    "tag_name": "v2026.9.8",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": "neurocade-release.json"}],
                },
            ]
        ),
        encoding="utf-8",
    )
    script = REPO_ROOT / "scripts/release/release_manifest.py"

    stable = subprocess.run(
        [str(script), "resolve", str(releases), "--channel", "stable"],
        check=True,
        text=True,
        capture_output=True,
    )
    beta = subprocess.run(
        [str(script), "resolve", str(releases), "--channel", "beta"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert stable.stdout.splitlines() == ["v2026.9.8", "false"]
    assert beta.stdout.splitlines() == ["v2026.9.9-beta.1", "true"]

    payload = json.loads(releases.read_text(encoding="utf-8"))
    payload.pop()
    releases.write_text(json.dumps(payload), encoding="utf-8")
    fallback = subprocess.run(
        [str(script), "resolve", str(releases), "--channel", "stable"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert fallback.stdout.splitlines() == ["v2026.9.9-beta.1", "true"]


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
            "--source",
            "neurocade-source-2026.8.30.tar.gz",
            "--source-revision",
            "a" * 40,
            "--output",
            str(assets / "neurocade-release.json"),
        ],
        check=True,
    )
    (assets / "github-releases.json").write_text(
        json.dumps(
            [
                {
                    "tag_name": "v2026.8.30",
                    "draft": False,
                    "prerelease": True,
                    "assets": [{"name": "neurocade-release.json"}],
                }
            ]
        ),
        encoding="utf-8",
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
        '[[ "$url" == */releases/latest/* ]] && exit 22\n'
        'if [[ "$url" == *api.github.com* ]]; then source="$FAKE_ASSET_DIR/github-releases.json"; '
        'else source="$FAKE_ASSET_DIR/${url##*/}"; fi\n'
        'exec /bin/cp "$source" "$target"\n',
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
    assert "No compatible stable Apptainer release was found; using v2026.8.30 instead." in result.stderr
    assert (checkout / ".runtime/images/neurocade-app-amd64.sif").read_bytes() == b"test-sif"
    assert result.stdout.splitlines()[-2:] == [
        str(checkout / ".runtime/release" / bridge_name),
        "2026.8.30",
    ]
