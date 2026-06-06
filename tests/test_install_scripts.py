"""Test install scripts behavior for NeuroCade."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"
APPTAINER_UP_SCRIPT = REPO_ROOT / "scripts" / "apptainer" / "up.sh"
APPTAINER_LIB_SCRIPT = REPO_ROOT / "scripts" / "apptainer" / "lib.sh"
APPTAINER_IMAGES_SCRIPT = REPO_ROOT / "scripts" / "apptainer" / "images.sh"
CONTAINERS_SCRIPT = REPO_ROOT / "scripts" / "containers.sh"
APPTAINER_DOWN_SCRIPT = REPO_ROOT / "scripts" / "apptainer" / "down.sh"
APPTAINER_STATUS_SCRIPT = REPO_ROOT / "scripts" / "apptainer" / "status.sh"
UPDATE_CHECKER_SCRIPT = REPO_ROOT / "scripts" / "update_checker.py"
RUN_DESKTOP_SCRIPT = REPO_ROOT / "scripts" / "desktop" / "run.sh"
INSTALL_DESKTOP_SCRIPT = REPO_ROOT / "scripts" / "desktop" / "install_launcher.sh"
RELEASE_STAGE_UPLOAD_SCRIPT = REPO_ROOT / "scripts" / "release" / "stage_upload_assets.sh"
TRAEFIK_DYNAMIC_CONFIG = REPO_ROOT / "config" / "traefik-dynamic.yml"
INSTALL_LIB_DIR = REPO_ROOT / "scripts" / "install"
ELECTRON_MAIN = REPO_ROOT / "client" / "electron" / "main.mjs"
CLIENT_MAIN = REPO_ROOT / "client" / "src" / "main.tsx"
BACKEND_STARTUP_GATE = REPO_ROOT / "client" / "src" / "components" / "BackendStartupGate.tsx"
CLIENT_PACKAGE = REPO_ROOT / "client" / "package.json"


def test_install_script_help_documents_copy_paste_and_modes() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "bash <(curl -fsSL" in result.stdout
    assert "--release-channel stable|prerelease|dev" in result.stdout
    assert "--prerelease" in result.stdout
    assert "--dev" in result.stdout
    assert "--mode local|internal|demo" in result.stdout
    assert "--desktop" in result.stdout
    assert "--no-desktop" in result.stdout
    assert "--with-freesurfer" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--doctor" in result.stdout
    assert "Authenticated institutional server" in result.stdout
    assert "Public sample-data instance" in result.stdout
    assert "NeuroCade interactive installer" in result.stdout


def test_raw_installer_bootstrap_clones_selected_release_channel(tmp_path: Path) -> None:
    script_path = tmp_path / "install.sh"
    script_path.write_text(INSTALL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_log = tmp_path / "git.log"
    exec_log = tmp_path / "exec.log"
    exec_env_log = tmp_path / "exec-env.log"
    fake_git = bin_dir / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$GIT_LOG"
case "${1:-}" in
  ls-remote)
    printf '1111111111111111111111111111111111111111\\trefs/tags/v2026.6.4\\n'
    printf '2222222222222222222222222222222222222222\\trefs/tags/v2026.10.1\\n'
    printf '3333333333333333333333333333333333333333\\trefs/tags/v2026.6.4-beta.1\\n'
    printf '4444444444444444444444444444444444444444\\trefs/tags/v2026.6.4-beta.2\\n'
    ;;
  clone)
    target="${@: -1}"
    mkdir -p "$target/scripts"
    {
      printf '%s\\n' '#!/usr/bin/env bash'
      printf '%s\\n' 'if [[ "$#" -gt 0 ]]; then'
      printf '%s\\n' '  printf "%s\\n" "$@" > "$EXEC_LOG"'
      printf '%s\\n' 'else'
      printf '%s\\n' '  : > "$EXEC_LOG"'
      printf '%s\\n' 'fi'
      printf '%s\\n' '{'
      printf '%s\\n' '  printf "repo=%s\\n" "$NEUROCADE_REPO_URL"'
      printf '%s\\n' '  printf "channel=%s\\n" "$NEUROCADE_INSTALL_CHANNEL"'
      printf '%s\\n' '} > "$EXEC_ENV_LOG"'
    } > "$target/scripts/install.sh"
    chmod +x "$target/scripts/install.sh"
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
if [[ "$url" == */download/*/bash-image-python-3.12.sif ]]; then
  rest="${url#*/download/}"
  tag="${rest%%/*}"
  if [[ " ${MISSING_RELEASE_ASSET_TAGS:-} " == *" $tag "* ]]; then
    exit 22
  fi
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    bootstrap_run_count = 0

    def run_bootstrap(*args: str, include_mode: bool = True, missing_release_asset_tags: str = "") -> str:
        nonlocal bootstrap_run_count
        bootstrap_run_count += 1
        git_log.write_text("", encoding="utf-8")
        exec_log.write_text("", encoding="utf-8")
        exec_env_log.write_text("", encoding="utf-8")
        command_args = [*args]
        if include_mode:
            command_args.extend(["--mode", "local"])
        install_dir = tmp_path / f"install-{bootstrap_run_count}"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GIT_LOG": str(git_log),
            "EXEC_LOG": str(exec_log),
            "EXEC_ENV_LOG": str(exec_env_log),
            "MISSING_RELEASE_ASSET_TAGS": missing_release_asset_tags,
            "NEUROCADE_INSTALL_DIR": str(install_dir),
            "NEUROCADE_REPO_URL": "https://example.invalid/Deep-MI/NeuroCade.git",
        }
        subprocess.run(["bash", str(script_path), *command_args], env=env, capture_output=True, text=True, check=True)
        return git_log.read_text(encoding="utf-8")

    stable_log = run_bootstrap()
    assert "clone --branch v2026.10.1 --depth 1 https://example.invalid/Deep-MI/NeuroCade.git" in stable_log
    assert exec_log.read_text(encoding="utf-8") == "--mode\nlocal\n"
    assert exec_env_log.read_text(encoding="utf-8") == (
        "repo=https://example.invalid/Deep-MI/NeuroCade.git\n"
        "channel=stable\n"
    )

    prerelease_log = run_bootstrap("--prerelease")
    assert "clone --branch v2026.6.4-beta.2 --depth 1 https://example.invalid/Deep-MI/NeuroCade.git" in prerelease_log
    assert exec_log.read_text(encoding="utf-8") == "--mode\nlocal\n"

    prerelease_fallback_log = run_bootstrap("--prerelease", missing_release_asset_tags="v2026.6.4-beta.2")
    assert "clone --branch v2026.6.4-beta.1 --depth 1 https://example.invalid/Deep-MI/NeuroCade.git" in prerelease_fallback_log
    assert exec_log.read_text(encoding="utf-8") == "--mode\nlocal\n"

    dev_log = run_bootstrap("--dev")
    assert "clone https://example.invalid/Deep-MI/NeuroCade.git" in dev_log
    assert "--branch" not in dev_log
    assert exec_log.read_text(encoding="utf-8") == "--mode\nlocal\n"

    no_forwarded_args_log = run_bootstrap(include_mode=False)
    assert "clone --branch v2026.10.1 --depth 1 https://example.invalid/Deep-MI/NeuroCade.git" in no_forwarded_args_log
    assert exec_log.read_text(encoding="utf-8") == ""


def test_install_shell_scripts_parse() -> None:
    subprocess.run(["bash", "-n", str(INSTALL_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(APPTAINER_UP_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(APPTAINER_IMAGES_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(CONTAINERS_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(APPTAINER_DOWN_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(APPTAINER_STATUS_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(RELEASE_STAGE_UPLOAD_SCRIPT)], check=True)
    subprocess.run([sys.executable, "-m", "py_compile", str(UPDATE_CHECKER_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(RUN_DESKTOP_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(INSTALL_DESKTOP_SCRIPT)], check=True)
    for lib_path in sorted(INSTALL_LIB_DIR.glob("*.sh")):
        subprocess.run(["bash", "-n", str(lib_path)], check=True)


def test_release_upload_staging_requires_bash_runtime_asset(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "release"
    upload_dir = tmp_path / "release-upload"
    artifact_dir.mkdir()
    (artifact_dir / "neurocade-client-v2026.6.6.tar.gz").write_text("client", encoding="utf-8")

    missing_result = subprocess.run(
        ["bash", str(RELEASE_STAGE_UPLOAD_SCRIPT)],
        env={**os.environ, "RELEASE_ARTIFACT_DIR": str(artifact_dir), "RELEASE_UPLOAD_DIR": str(upload_dir)},
        capture_output=True,
        text=True,
    )

    assert missing_result.returncode != 0
    assert "Required runtime release asset is missing" in missing_result.stderr

    (artifact_dir / "bash-image-python-3.12.sif").write_text("runtime", encoding="utf-8")
    ok_result = subprocess.run(
        ["bash", str(RELEASE_STAGE_UPLOAD_SCRIPT)],
        env={**os.environ, "RELEASE_ARTIFACT_DIR": str(artifact_dir), "RELEASE_UPLOAD_DIR": str(upload_dir)},
        capture_output=True,
        text=True,
        check=True,
    )

    assert "bash-image-python-3.12.sif" in ok_result.stdout
    assert (upload_dir / "bash-image-python-3.12.sif").read_text(encoding="utf-8") == "runtime"


def test_lima_vm_size_defaults_reserve_host_resources() -> None:
    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "common.sh"}
source {INSTALL_LIB_DIR / "apptainer.sh"}
printf 'memory_large=%s\\n' "$(default_lima_memory_gib 32)"
printf 'memory_small=%s\\n' "$(default_lima_memory_gib 8)"
printf 'cpus_large=%s\\n' "$(default_lima_cpus 10)"
printf 'cpus_small=%s\\n' "$(default_lima_cpus 4)"
printf 'disk_large=%s\\n' "$(default_lima_disk_gib 500)"
printf 'disk_small=%s\\n' "$(default_lima_disk_gib 80)"
printf 'disk_low=%s\\n' "$(default_lima_disk_gib 32)"
"""

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)

    assert "memory_large=12" in result.stdout
    assert "memory_small=4" in result.stdout
    assert "cpus_large=4" in result.stdout
    assert "cpus_small=3" in result.stdout
    assert "disk_large=100" in result.stdout
    assert "disk_small=60" in result.stdout
    assert "disk_low=12" in result.stdout


def test_install_choice_menu_is_visible_when_selection_is_captured() -> None:
    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "common.sh"}
is_tty() {{ return 0; }}
choice="$(printf '2\\n' | choose "Deployment mode" "local" \
  "local|Single user on this machine." \
  "lan|Trusted local network." \
  "internet|Public HTTPS behind a proxy.")"
printf 'choice=%s\\n' "$choice"
"""

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)

    assert result.stdout == "choice=lan\n"
    assert "Deployment mode" in result.stderr
    assert "1. local" in result.stderr
    assert "2. lan" in result.stderr
    assert "Single user on this machine." in result.stderr
    assert "Trusted local network." in result.stderr
    assert "Select option [local]:" in result.stderr


def test_env_example_exposes_installer_managed_settings() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    for key in [
        "DEPLOYMENT_PROFILE=",
        "CLIENT_SERVE_MODE=",
        "APP_BASE_URL=",
        "APP_PUBLIC_URL=",
        "APP_ALLOWED_HOSTS=",
        "APP_HTTP_BIND=",
        "NEUROCADE_VERSION=",
        "NEUROCADE_VERSION_CHECK_URL=",
        "NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS=",
        "APPTAINER_BIN=",
        "NEUROCADE_CONTAINER_RELEASE_TAG=",
        "NEUROCADE_CONTAINER_ROOT=",
        "NEUROCADE_CONTAINER_INVENTORY=",
        "NEUROCADE_INSTALLED_TOOLS_JSONL=",
        "POSTGRES_SIF=",
        "REDIS_URL=",
        "DATABASE_URL=",
        "LLM_NATIVE_TOOL_CALLING=",
        "MAX_UPLOAD_FILE_SIZE_BYTES=",
        "DICOM_ZIP_MAX_ENTRIES=",
        "ANTHROPIC_API_KEY=",
        "GOOGLE_API_KEY=",
        "OLLAMA_BASE_URL=",
    ]:
        assert key in env_example
    assert "NEURO_CLI_RECORDS_JSONL=" not in env_example
    assert (REPO_ROOT / "migrations" / "env.py").is_file()


def test_apptainer_launcher_is_rootless_and_port_driven() -> None:
    launcher_text = APPTAINER_UP_SCRIPT.read_text(encoding="utf-8")
    image_text = APPTAINER_IMAGES_SCRIPT.read_text(encoding="utf-8")
    lib_text = APPTAINER_LIB_SCRIPT.read_text(encoding="utf-8")

    assert "images.sh\" infra" in launcher_text
    assert "containers.sh\" install core" in launcher_text
    assert "docker compose" not in launcher_text
    assert "--fakeroot" not in launcher_text
    assert "Python runtime dependencies already installed." in launcher_text
    assert "UV_CACHE_DIR" in launcher_text
    assert "start_service update-checker" in launcher_text
    assert "stop_host_orphan" in lib_text
    assert "Stopping stale $name listener on port $port" in lib_text
    assert "ensure_lima_checkout_mount_live_writable" in launcher_text
    assert 'TRAEFIK_ENTRYPOINT_BIND="0.0.0.0"' in launcher_text
    assert '--entrypoints.web.address="$TRAEFIK_ENTRYPOINT_BIND:$APP_HTTP_PORT"' in launcher_text
    assert "serve_static_client.py" in launcher_text
    assert "POSTGRES_PORT" in launcher_text
    assert "Invalid Traefik dashboard configuration." in launcher_text
    assert 'TRAEFIK_API_INSECURE:-false}" == "true" && "$TRAEFIK_DASHBOARD_PORT" == "8080"' in launcher_text
    assert "PathPrefix(`\\/v1`)" not in launcher_text
    assert "PathPrefix(`/v1`)" not in TRAEFIK_DYNAMIC_CONFIG.read_text(encoding="utf-8")
    assert "openai-compat" not in launcher_text
    assert "openai-compat" not in TRAEFIK_DYNAMIC_CONFIG.read_text(encoding="utf-8")
    assert "curl -fL" in image_text
    assert "fakeroot preflight failed" in image_text
    assert "Runtime/tool containers are" in image_text


def test_electron_launcher_is_local_stack_wrapper() -> None:
    package_text = CLIENT_PACKAGE.read_text(encoding="utf-8")
    electron_text = ELECTRON_MAIN.read_text(encoding="utf-8")
    install_text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    common_text = (INSTALL_LIB_DIR / "common.sh").read_text(encoding="utf-8")
    install_bundle_text = install_text + "\n" + "\n".join(path.read_text(encoding="utf-8") for path in INSTALL_LIB_DIR.glob("*.sh"))
    runner_text = RUN_DESKTOP_SCRIPT.read_text(encoding="utf-8")

    assert '"main": "electron/main.mjs"' in package_text
    assert '"electron:local": "electron ./electron/main.mjs"' in package_text
    assert "'apptainer', 'up.sh'" in electron_text
    assert "/api/app/healthz" in electron_text
    assert "disable-gpu-sandbox" in electron_text
    assert "sandbox: !chromiumSandboxDisabled" in electron_text
    assert "'apptainer', 'down.sh'" in electron_text
    assert "startedStack" in electron_text
    assert "const healthTimeoutMs = 600_000" in electron_text
    assert "API is healthy at" in electron_text
    assert "apiServiceWasHealthy" in electron_text
    assert "but the app gateway is not responding" in electron_text
    assert "DESKTOP_MODE=auto" in install_text
    assert "source \"$INSTALL_LIB_DIR/common.sh\"" in install_text
    assert "run_doctor" in install_text
    assert "setup_install_logging" in install_text
    assert '[[ "$mode" == "local" ]]' in install_text
    assert "freesurfer_license_available" in install_bundle_text
    assert "FREESURFER_LICENSE_URL" in install_bundle_text
    assert "https://surfer.nmr.mgh.harvard.edu/registration.html" in install_bundle_text
    assert "env_file_value" in install_bundle_text
    assert "ensure_node" in install_text
    assert "install/node.sh" in "\n".join(str(path.relative_to(REPO_ROOT)) for path in INSTALL_LIB_DIR.glob("*.sh"))
    assert "Installing Node.js locally" in install_bundle_text
    assert "install_lima_macos_local" in install_bundle_text
    assert "NEUROCADE_INSTALL_TELEMETRY_URL" not in install_bundle_text
    assert "git clone \"$REPO_URL\"" not in common_text
    assert "start_sample_case_prefetch" in install_text
    assert "wait_sample_case_prefetch" in install_text
    assert "sample-case-prefetch.log" in install_text
    assert 'run_step "Installing infrastructure images" "$root/scripts/apptainer/images.sh" infra' in install_text
    assert "process_demo_case.sh" not in install_text
    assert 'demo_default="n"' not in install_text
    assert "Falling back to building the demo/sample case locally." not in install_text
    assert "A FreeSurfer license is highly recommended" in install_bundle_text
    assert "install_apptainer_linux" in install_bundle_text
    assert "install_apptainer_macos" in install_bundle_text
    assert ".node/bin" in runner_text
    assert "START_STACK=0" in install_text
    assert "--no-sandbox --disable-gpu-sandbox --disable-setuid-sandbox" in runner_text
    assert '"$CLIENT_DIR/node_modules/.bin/electron" "${electron_args[@]}"' in runner_text


def test_installer_passes_freesurfer_opt_in_to_container_install() -> None:
    install_text = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert 'NEUROCADE_INSTALL_FREESURFER:-}" == "1"' in install_text
    assert "--with-freesurfer)" in install_text
    assert "INSTALL_FREESURFER=1" in install_text
    assert 'prefetch_command=("$root/scripts/containers.sh" prefetch core)' in install_text
    assert 'start_sample_case_prefetch "$root"' in install_text
    assert 'run_step "Installing infrastructure images" "$root/scripts/apptainer/images.sh" infra' in install_text
    assert '[[ "$INSTALL_FREESURFER" -eq 1 ]] && freesurfer_license_available "$root"' in install_text
    assert 'prefetch_command+=(--with-freesurfer)' in install_text
    assert '"${prefetch_command[@]}"' in install_text
    assert 'install_command=("$root/scripts/containers.sh" install core --source auto)' in install_text
    assert 'install_command+=(--with-freesurfer)' in install_text
    assert 'run_step "Installing core runtime containers" "${install_command[@]}"' in install_text


def _make_sample_case_archive(tmp_path: Path) -> Path:
    sample_root = tmp_path / "sample-archive-root" / "FastSurfer_Rhineland_0000"
    for relative in [
        "mri/orig.mgz",
        "mri/aparc.DKTatlas+aseg.deep.mgz",
        "surf/lh.pial",
        "logs/stderr.log",
    ]:
        target = sample_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{relative}\n", encoding="utf-8")
    archive = tmp_path / "sample-case.tar.gz"
    subprocess.run(
        ["tar", "-C", str(sample_root.parent), "-czf", str(archive), sample_root.name],
        check=True,
    )
    return archive


def _make_minimal_installer_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    install_dir = root / "scripts" / "install"
    install_dir.mkdir(parents=True)
    (root / "scripts" / "apptainer").mkdir(parents=True)
    (root / "client").mkdir()
    (root / "client" / "package.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")
    (root / "scripts" / "install.sh").write_text(INSTALL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "scripts" / "install.sh").chmod(0o755)
    (root / "scripts" / "apptainer" / "up.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / "scripts" / "apptainer" / "up.sh").chmod(0o755)
    (root / "scripts" / "apptainer" / "images.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / "scripts" / "apptainer" / "images.sh").chmod(0o755)
    (root / "scripts" / "containers.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / "scripts" / "containers.sh").chmod(0o755)
    (install_dir / "common.sh").write_text(
        """#!/usr/bin/env bash
is_tty() { return 1; }
choose() { printf '%s\\n' "$2"; }
require_supported_os() { :; }
ensure_prerequisites() { :; }
ensure_checkout() { pwd; }
env_file_value() { printf '\\n'; }
normalize_mode() { printf '%s\\n' "$1"; }
normalize_provider() { printf '%s\\n' "$1"; }
setup_install_logging() { :; }
log_section() { echo "==> $*"; }
run_step() {
  local label="$1"
  shift
  "$@"
  echo "<== $label complete"
}
""",
        encoding="utf-8",
    )
    (install_dir / "python.sh").write_text("ensure_python_runtime() { :; }\n", encoding="utf-8")
    (install_dir / "node.sh").write_text(
        "ensure_node() { :; }\nclient_dependencies_current() { return 0; }\n",
        encoding="utf-8",
    )
    (install_dir / "lima.sh").write_text("", encoding="utf-8")
    (install_dir / "apptainer.sh").write_text("ensure_apptainer() { :; }\n", encoding="utf-8")
    (install_dir / "env.sh").write_text(
        """write_env() {
  mkdir -p "$1"
  printf 'APP_BASE_URL=http://localhost:8005\\n' > "$1/.env"
}
freesurfer_license_available() { return 1; }
""",
        encoding="utf-8",
    )
    (install_dir / "doctor.sh").write_text("run_doctor() { :; }\n", encoding="utf-8")
    return root


def _run_sample_case_install(
    tmp_path: Path,
    *,
    channel: str,
    container_release_tag: str,
    available_sample_tags: list[str],
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    root = _make_minimal_installer_checkout(tmp_path)
    archive = _make_sample_case_archive(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_log = tmp_path / "curl.log"
    available_tags_file = tmp_path / "available-tags.txt"
    available_tags_file.write_text("\n".join(available_sample_tags) + "\n", encoding="utf-8")
    fake_git = bin_dir / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  ls-remote)
    printf '1111111111111111111111111111111111111111\\trefs/tags/v2026.5.1\\n'
    printf '2222222222222222222222222222222222222222\\trefs/tags/v2026.6.4\\n'
    printf '3333333333333333333333333333333333333333\\trefs/tags/v2026.10.1\\n'
    printf '4444444444444444444444444444444444444444\\trefs/tags/v2026.6.4-beta.1\\n'
    printf '5555555555555555555555555555555555555555\\trefs/tags/v2026.6.4-beta.2\\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
out=""
url=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -o)
      out="$2"
      shift 2
      ;;
    -*)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf '%s\\n' "$url" >> "$CURL_LOG"
if [[ "$url" == */"$SAMPLE_CASE_ARTIFACT_NAME" ]]; then
  tag=""
  case "$url" in
    */latest/download/*)
      tag="latest"
      ;;
    */download/*/*)
      rest="${url#*/download/}"
      tag="${rest%%/*}"
      ;;
  esac
  if grep -Fxq "$tag" "$AVAILABLE_SAMPLE_TAGS"; then
    if [[ -n "$out" ]]; then
      cp "$TAR_ARCHIVE" "$out"
    fi
    exit 0
  fi
fi
exit 22
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CURL_LOG": str(curl_log),
        "TAR_ARCHIVE": str(archive),
        "AVAILABLE_SAMPLE_TAGS": str(available_tags_file),
        "SAMPLE_CASE_ARTIFACT_NAME": "neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
        "NEUROCADE_CONTAINER_RELEASE_TAG": container_release_tag,
    }
    result = subprocess.run(
        [
            "bash",
            "scripts/install.sh",
            "--mode",
            "local",
            "--llm-provider",
            "ollama",
            "--release-channel",
            channel,
            "--no-prereqs",
            "--no-start",
            "--no-desktop",
            "--yes",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    sample_log = root / ".runtime" / "logs" / "sample-case-prefetch.log"
    return result, curl_log.read_text(encoding="utf-8").splitlines(), sample_log.read_text(encoding="utf-8")


def test_sample_case_download_uses_chosen_release_when_asset_exists(tmp_path: Path) -> None:
    result, curl_urls, sample_log = _run_sample_case_install(
        tmp_path,
        channel="prerelease",
        container_release_tag="v2026.6.4-beta.2",
        available_sample_tags=["v2026.6.4-beta.2", "v2026.6.4-beta.1"],
    )

    sample_urls = [url for url in curl_urls if url.endswith("neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz")]
    assert sample_urls == [
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4-beta.2/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4-beta.2/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
    ]
    assert "scanning older release assets" not in result.stderr
    assert "scanning older release assets" not in sample_log


def test_sample_case_download_scans_older_beta_assets_for_prerelease(tmp_path: Path) -> None:
    result, curl_urls, sample_log = _run_sample_case_install(
        tmp_path,
        channel="prerelease",
        container_release_tag="v2026.6.4-beta.2",
        available_sample_tags=["v2026.6.4-beta.1"],
    )

    sample_urls = [url for url in curl_urls if url.endswith("neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz")]
    assert sample_urls == [
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4-beta.2/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4-beta.1/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4-beta.1/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
    ]
    assert "Using demo/sample case asset from v2026.6.4-beta.1." in sample_log
    assert all("/v2026.10.1/" not in url for url in sample_urls)


def test_sample_case_download_scans_older_stable_assets_for_stable_channel(tmp_path: Path) -> None:
    result, curl_urls, sample_log = _run_sample_case_install(
        tmp_path,
        channel="stable",
        container_release_tag="v2026.10.1",
        available_sample_tags=["v2026.6.4"],
    )

    sample_urls = [url for url in curl_urls if url.endswith("neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz")]
    assert sample_urls == [
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.10.1/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
        "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.4/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
    ]
    assert "Using demo/sample case asset from v2026.6.4." in sample_log
    assert all("-beta." not in url for url in sample_urls)


def test_sample_case_download_warns_and_skips_when_no_release_asset_exists(tmp_path: Path) -> None:
    result, curl_urls, sample_log = _run_sample_case_install(
        tmp_path,
        channel="stable",
        container_release_tag="v2026.10.1",
        available_sample_tags=[],
    )

    sample_urls = [url for url in curl_urls if url.endswith("neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz")]
    assert sample_urls
    assert "scanning older release assets" in sample_log
    assert "Warning: demo/sample case artifact could not be found or downloaded; skipping sample case." in result.stderr


def test_local_installer_disables_traefik_dashboard_by_default(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "client").mkdir(parents=True)
    (root / "client" / "package.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")

    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "common.sh"}
source {INSTALL_LIB_DIR / "env.sh"}
APP_DISPLAY_NAME=NeuroCade
FREESURFER_LICENSE_URL=https://surfer.nmr.mgh.harvard.edu/registration.html
write_env {root} local ollama
"""

    subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    env_text = (root / ".env").read_text(encoding="utf-8")

    assert "TRAEFIK_DASHBOARD_ENABLED=false" in env_text
    assert "TRAEFIK_API_INSECURE=false" in env_text


def test_client_dependencies_current_checks_electron_and_lockfile_age(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    client = root / "client"
    electron_bin = client / "node_modules" / ".bin" / "electron"
    installed_lock = client / "node_modules" / ".package-lock.json"
    package_json = client / "package.json"
    package_lock = client / "package-lock.json"
    electron_bin.parent.mkdir(parents=True)
    package_json.write_text("{}\n", encoding="utf-8")
    package_lock.write_text("{}\n", encoding="utf-8")
    installed_lock.write_text("{}\n", encoding="utf-8")
    electron_bin.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    electron_bin.chmod(0o755)
    os.utime(package_json, (1, 1))
    os.utime(package_lock, (1, 1))
    os.utime(installed_lock, (2, 2))

    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "node.sh"}
if client_dependencies_current {root}; then
  printf 'current\\n'
else
  printf 'stale\\n'
fi
touch {package_lock}
if client_dependencies_current {root}; then
  printf 'current\\n'
else
  printf 'stale\\n'
fi
"""

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)

    assert result.stdout == "current\nstale\n"


def test_install_doctor_and_dry_run_do_not_start_stack() -> None:
    doctor = subprocess.run(
        [
            "bash",
            str(INSTALL_SCRIPT),
            "--doctor",
            "--mode",
            "local",
            "--llm-provider",
            "ollama",
            "--yes",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "NeuroCade installer doctor" in doctor.stdout
    assert "Actions the installer may take" in doctor.stdout
    assert "Deployment checks" in doctor.stdout
    assert "Runtime network containment" in doctor.stdout
    assert "update checks" in doctor.stdout.lower()

    dry_run = subprocess.run(
        [
            "bash",
            str(INSTALL_SCRIPT),
            "--dry-run",
            "--mode",
            "local",
            "--llm-provider",
            "ollama",
            "--yes",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Dry run only" in dry_run.stdout
    assert "No files were written" in dry_run.stdout


def test_update_checker_logs_only_when_new_version_is_available(tmp_path: Path) -> None:
    latest = tmp_path / "latest.json"
    latest.write_text('{"version":"9.9.9","url":"https://example.org/neurocade"}', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(UPDATE_CHECKER_SCRIPT),
            "--once",
        ],
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
            "NEUROCADE_VERSION": "0.0.0",
            "NEUROCADE_VERSION_CHECK_URL": latest.as_uri(),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[NeuroCade update] Version 9.9.9 is available" in result.stdout

    unreachable = subprocess.run(
        [
            sys.executable,
            str(UPDATE_CHECKER_SCRIPT),
            "--once",
        ],
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
            "NEUROCADE_VERSION": "0.0.0",
            "NEUROCADE_VERSION_CHECK_URL": "http://127.0.0.1:9/latest.json",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert unreachable.stdout == ""
    assert unreachable.stderr == ""


def test_env_writer_quotes_shell_sensitive_values() -> None:
    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "env.sh"}
env_line LOCAL_AUTH_NAME "Demo User"
env_line LLM_BACKEND_MODEL "07 - Qwen3.5-35B-A3B - Multimodal model from Feb 2026"
env_line APP_BASE_URL "http://localhost:8005"
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert 'LOCAL_AUTH_NAME="Demo User"' in result.stdout
    assert 'LLM_BACKEND_MODEL="07 - Qwen3.5-35B-A3B - Multimodal model from Feb 2026"' in result.stdout
    assert "APP_BASE_URL=http://localhost:8005" in result.stdout


def test_env_writer_reuses_existing_env_values_without_prompting(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "client").mkdir(parents=True)
    (root / "client" / "package.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")
    (root / ".env").write_text(
        "\n".join(
            [
                "APP_BASE_URL=http://localhost:9000",
                "APP_PUBLIC_URL=http://localhost:9001",
                "APP_ALLOWED_HOSTS=custom.local",
                "APP_HTTP_BIND=0.0.0.0",
                "APP_HTTP_PORT=9000",
                "POSTGRES_PASSWORD=keep-postgres",
                "REDIS_PASSWORD=keep-redis",
                "LLM_API_TOKEN=keep-token",
                "LLM_BACKEND_URL=https://llm.example.test/v1",
                "LLM_BACKEND_API_KEY=keep-key",
                "LLM_BACKEND_MODEL=keep-model",
                "LLM_NATIVE_TOOL_CALLING=true",
                "FREESURFER_LICENSE=",
                "",
            ]
        ),
        encoding="utf-8",
    )

    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "common.sh"}
source {INSTALL_LIB_DIR / "env.sh"}
APP_DISPLAY_NAME=NeuroCade
FREESURFER_LICENSE_URL=https://surfer.nmr.mgh.harvard.edu/registration.html
prompt() {{ echo "unexpected prompt: $1" >&2; exit 90; }}
confirm() {{ echo "unexpected confirm: $1" >&2; exit 91; }}
write_env {root} local openai-compatible
"""

    subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    env_text = (root / ".env").read_text(encoding="utf-8")

    assert "APP_BASE_URL=http://localhost:9000" in env_text
    assert "APP_PUBLIC_URL=http://localhost:9001" in env_text
    assert "APP_ALLOWED_HOSTS=custom.local" in env_text
    assert "APP_HTTP_BIND=0.0.0.0" in env_text
    assert "APP_HTTP_PORT=9000" in env_text
    assert "POSTGRES_PASSWORD=keep-postgres" in env_text
    assert "REDIS_PASSWORD=keep-redis" in env_text
    assert "LLM_API_TOKEN=keep-token" in env_text
    assert "LLM_BACKEND_URL=https://llm.example.test/v1" in env_text
    assert "LLM_BACKEND_API_KEY=keep-key" in env_text
    assert "LLM_BACKEND_MODEL=keep-model" in env_text
    assert "LLM_BACKEND_REQUIRES_API_KEY" not in env_text
    assert "LLM_NATIVE_TOOL_CALLING=true" in env_text
    assert "FREESURFER_LICENSE=" in env_text
    assert list(root.glob(".env.backup.*"))


def test_env_writer_uses_neurocade_postgres_defaults_for_new_installs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "client").mkdir(parents=True)
    (root / "client" / "package.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")

    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "common.sh"}
source {INSTALL_LIB_DIR / "env.sh"}
APP_DISPLAY_NAME=NeuroCade
FREESURFER_LICENSE_URL=https://surfer.nmr.mgh.harvard.edu/registration.html
write_env {root} local openai-compatible
"""

    subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    env_text = (root / ".env").read_text(encoding="utf-8")

    assert "POSTGRES_USER=neurocade_user" in env_text
    assert "POSTGRES_DB=neurocade_db" in env_text
    assert "DATABASE_URL=postgresql+psycopg://neurocade_user:" in env_text
    assert "@127.0.0.1:55432/neurocade_db" in env_text


def test_installer_detects_freesurfer_license_path(tmp_path: Path) -> None:
    license_path = tmp_path / "license.txt"
    license_path.write_text("license-data\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()

    script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "env.sh"}
export FREESURFER_LICENSE={license_path}
printf 'license_default=%s\\n' "$(detect_freesurfer_license_default {root})"
printf 'license=%s\\n' "$(freesurfer_license_path {root})"
install_freesurfer_license_if_available {root} {root / "neurocade-data"} "$FREESURFER_LICENSE"
printf 'copied=%s\\n' "$(cat {root / "neurocade-data" / "license.txt"})"
"""

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)

    assert f"license_default={license_path}" in result.stdout
    assert f"license={license_path}" in result.stdout
    assert "Detected FreeSurfer license" in result.stdout
    assert "Copied FreeSurfer license" in result.stdout
    assert "copied=license-data" in result.stdout


def test_installer_detects_freesurfer_home_license_candidates(tmp_path: Path) -> None:
    for index, license_name in enumerate(("license.txt", ".license", ".license.txt")):
        fs_home = tmp_path / f"freesurfer-{index}"
        fs_home.mkdir()
        license_path = fs_home / license_name
        license_path.write_text(f"license-data-{index}\n", encoding="utf-8")
        root = tmp_path / f"repo-{index}"
        root.mkdir()

        script = f"""
set -euo pipefail
source {INSTALL_LIB_DIR / "env.sh"}
unset FREESURFER_LICENSE
export FREESURFER_HOME={fs_home}
printf 'license_default=%s\\n' "$(detect_freesurfer_license_default {root})"
printf 'license=%s\\n' "$(freesurfer_license_path {root})"
install_freesurfer_license_if_available {root} {root / "neurocade-data"}
printf 'copied=%s\\n' "$(cat {root / "neurocade-data" / "license.txt"})"
"""

        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)

        assert f"license_default={license_path}" in result.stdout
        assert f"license={license_path}" in result.stdout
        assert "Detected FreeSurfer license" in result.stdout
        assert "Copied FreeSurfer license" in result.stdout
        assert f"copied=license-data-{index}" in result.stdout


def test_browser_startup_gate_waits_for_backend_health() -> None:
    main_text = CLIENT_MAIN.read_text(encoding="utf-8")
    gate_text = BACKEND_STARTUP_GATE.read_text(encoding="utf-8")

    assert "BackendStartupGate" in main_text
    assert "<BackendStartupGate>" in main_text
    assert "const startupTimeoutMs = 600_000" in gate_text
    assert "const healthPollMs = 1500" in gate_text
    assert "const healthRequestTimeoutMs = 2500" in gate_text
    assert "Frontend is ready. Backend is not connected yet" in gate_text
    assert "Open local gateway" in gate_text
    assert "Retry connection" in gate_text
    assert "fetch(url" in gate_text
