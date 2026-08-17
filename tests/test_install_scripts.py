"""Test Docker-only install and launcher scripts."""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_installer_checkout(checkout: Path) -> Path:
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "install.sh", scripts_dir / "install.sh")
    run_script = scripts_dir / "run.sh"
    run_script.write_text(
        "#!/usr/bin/env bash\n"
        '[[ -z "${INSTALL_RUN_LOG:-}" ]] || printf "%s\\n" "$*" >> "$INSTALL_RUN_LOG"\n',
        encoding="utf-8",
    )
    run_script.chmod(0o755)
    return scripts_dir


def _write_fake_docker(bin_dir: Path, log_file: Path) -> Path:
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_RUN_LOG"\n'
        'case "$1:$2" in\n'
        '  image:inspect)\n'
        '    [[ "${FAKE_DOCKER_IMAGE_PRESENT:-true}" == "true" ]] || exit 1\n'
        '    if [[ "${3:-}" == "--format" ]]; then\n'
        '      printf "%s\\n" "${FAKE_DOCKER_IMAGE_PLATFORM:-linux/amd64}"\n'
        "    fi\n"
        "    exit 0 ;;\n"
        "  info:*) exit 0 ;;\n"
        "  build:*) exit 0 ;;\n"
        "  pull:*) exit 0 ;;\n"
        '  ps:*) [[ -f "${DOCKER_RUN_LOG}.container" ]] && printf "fake-container-id\\n"; exit 0 ;;\n'
        "  exec:*) exit 0 ;;\n"
        "  rm:-f) exit 0 ;;\n"
        '  run:*)\n'
        '    if [[ "$*" == *"--gpus all"* && "${FAKE_DOCKER_GPU_AVAILABLE:-true}" != "true" ]]; then exit 1; fi\n'
        '    [[ "$*" == *"--name neurocade"* ]] && touch "${DOCKER_RUN_LOG}.container"\n'
        '    exit 0 ;;\n'
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return docker


def _launcher_env(env_file: Path, bin_dir: Path, log_file: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "HOST_DATA_DIR",
        "NEUROCADE_DB_DIR",
        "NEUROCADE_IMAGE",
        "NEUROCADE_DOCKER_PLATFORM",
        "NEUROCADE_SAMPLE_CASE_URL",
        "NEUROCADE_SAMPLE_CASE_SHA256",
        "NEUROCADE_SKIP_SAMPLE_CASE",
        "NEUROCADE_GPU_MODE",
        "NEUROCADE_STARTUP_TIMEOUT_SECONDS",
        "NEUROCADE_UID",
        "NEUROCADE_GID",
        "FREESURFER_LICENSE",
    ):
        env.pop(key, None)
    env.update(
        {
            "DOCKER_RUN_LOG": str(log_file),
            "ENV_FILE": str(env_file),
            "NEUROCADE_FUSE_DEVICE": "/dev/null",
            "NEUROCADE_MIN_FREE_KB": "1",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            **overrides,
        }
    )
    return env


def test_shell_entrypoints_parse() -> None:
    scripts = [
        "scripts/install.sh",
        "scripts/lib/env.sh",
        "scripts/build_image.sh",
        "scripts/run.sh",
        "scripts/desktop/run.sh",
        "scripts/admin/reset_app_state.sh",
        "scripts/release/compute_tag.sh",
    ]
    for script in scripts:
        subprocess.run(["bash", "-n", str(REPO_ROOT / script)], check=True)


def test_install_scripts_drop_legacy_sidecar_config() -> None:
    script_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            REPO_ROOT / "scripts" / "install.sh",
            REPO_ROOT / "scripts" / "run.sh",
            REPO_ROOT / "scripts" / "desktop" / "run.sh",
        ]
    )

    # The monolith has no external runtime or queue services to configure.
    legacy_terms = [
        "runtime_" + "cache_env.sh",
        "HOST_" + "RUNTIME_RUNNER_",
        "RUNTIME_" + "RUNNER_TOKEN",
        "POSTGRES_" + "PASSWORD",
        "REDIS_" + "URL",
    ]
    for term in legacy_terms:
        assert term not in script_text


def test_docker_launcher_replaces_compose() -> None:
    run_script = (REPO_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")

    assert "docker run" in run_script
    assert "--privileged" in run_script
    assert "--device /dev/fuse" in run_script
    assert "APP_HTTP_PORT" in run_script
    assert "docker compose" not in run_script
    assert run_script.index("load_env_file") < run_script.index('CONTAINER_NAME="${NEUROCADE_CONTAINER_NAME:-neurocade}"')


def test_docker_launcher_accepts_explicit_port(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    (checkout / ".env").write_text("NEUROCADE_SKIP_SAMPLE_CASE=true\n", encoding="utf-8")

    env = _launcher_env(checkout / ".env", bin_dir, log_file)
    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "start", "--port", "9123"],
        check=True,
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
    )

    assert "-p 127.0.0.1:9123:8000" in log_file.read_text(encoding="utf-8")
    assert "-e NEUROCADE_ACCESS_URL=http://127.0.0.1:9123" in log_file.read_text(encoding="utf-8")
    log_text = log_file.read_text(encoding="utf-8")
    assert f"--user {os.getuid()}:{os.getgid()}" in log_text
    assert "/.neurocade/passwd:/etc/passwd:ro" in log_text
    assert "/.neurocade/group:/etc/group:ro" in log_text
    assert "--gpus all" in log_text
    assert "python -m api_service.runtime_tools.prepare_images" in log_text
    assert "Starting NeuroCade at http://127.0.0.1:9123" in result.stdout
    assert "\x1b" not in result.stdout


def test_detached_launcher_waits_for_backend_before_printing_url(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    (checkout / ".env").write_text(
        "NEUROCADE_SKIP_SAMPLE_CASE=true\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "start", "-d"],
        check=True,
        cwd=checkout,
        env=_launcher_env(checkout / ".env", bin_dir, log_file),
        text=True,
        capture_output=True,
    )

    log_lines = log_file.read_text(encoding="utf-8").splitlines()
    assert next(index for index, line in enumerate(log_lines) if line.startswith("exec neurocade")) > next(
        index for index, line in enumerate(log_lines) if line.startswith("run --name neurocade")
    )
    assert "NeuroCade is ready at http://127.0.0.1:" in result.stdout
    assert "Starting NeuroCade at" not in result.stdout


def test_docker_launcher_cpu_mode_skips_gpu_passthrough(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    (checkout / ".env").write_text(
        "NEUROCADE_SKIP_SAMPLE_CASE=true\nNEUROCADE_GPU_MODE=cpu\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "start"],
        check=True,
        cwd=checkout,
        env=_launcher_env(checkout / ".env", bin_dir, log_file),
        text=True,
        capture_output=True,
    )

    assert "--gpus all" not in log_file.read_text(encoding="utf-8")
    assert "GPU mode: cpu" in result.stdout


def test_docker_launcher_required_cuda_fails_before_app_start(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    (checkout / ".env").write_text(
        "NEUROCADE_SKIP_SAMPLE_CASE=true\nNEUROCADE_GPU_MODE=cuda\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "start"],
        cwd=checkout,
        env=_launcher_env(
            checkout / ".env",
            bin_dir,
            log_file,
            FAKE_DOCKER_GPU_AVAILABLE="false",
        ),
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "CUDA was requested" in result.stderr
    assert "run --name neurocade" not in log_file.read_text(encoding="utf-8")


def test_doctor_fails_when_application_image_is_missing(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    (checkout / ".env").write_text("NEUROCADE_SKIP_SAMPLE_CASE=true\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "doctor"],
        cwd=checkout,
        env=_launcher_env(
            checkout / ".env",
            bin_dir,
            log_file,
            FAKE_DOCKER_IMAGE_PRESENT="false",
        ),
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Apptainer and image-integrity checks cannot run" in result.stderr


def test_doctor_rejects_a_non_device_fuse_path(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    fake_fuse = tmp_path / "fuse"
    fake_fuse.touch()
    (checkout / ".env").write_text("NEUROCADE_SKIP_SAMPLE_CASE=true\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "doctor"],
        cwd=checkout,
        env=_launcher_env(
            checkout / ".env",
            bin_dir,
            log_file,
            NEUROCADE_FUSE_DEVICE=str(fake_fuse),
        ),
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "FUSE character device is missing" in result.stderr


def test_docker_launcher_selects_next_available_port(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)
    ss = bin_dir / "ss"
    ss.write_text(
        "#!/usr/bin/env bash\n"
        '[[ "$*" == *":${FAKE_OCCUPIED_PORT}"* ]] && printf "LISTEN occupied\\n"\n',
        encoding="utf-8",
    )
    ss.chmod(0o755)
    (checkout / ".env").write_text("NEUROCADE_SKIP_SAMPLE_CASE=true\n", encoding="utf-8")

    env = _launcher_env(
        checkout / ".env",
        bin_dir,
        log_file,
        FAKE_OCCUPIED_PORT="8000",
    )
    result = subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "start"],
        check=True,
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
    )

    assert "-p 127.0.0.1:8001:8000" in log_file.read_text(encoding="utf-8")
    assert "Port 8000 is already in use; using port 8001 instead." in result.stdout


def test_docker_launcher_rejects_invalid_port() -> None:
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "run.sh"), "start", "--port", "70000"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "Invalid port: 70000" in result.stderr


def test_build_image_is_independent_of_runtime_auth_env(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LOCAL_AUTH_ENABLED=false",
                'CLERK_PUBLISHABLE_KEY="pk_from_env"',
                "CLERK_JWT_TEMPLATE=neurocade-template",
                "NEUROCADE_DOCKER_PLATFORM=linux/amd64",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "env.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$BUILD_IMAGE_ENV_LOG"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = os.environ.copy()
    for leaked_key in (
        "LOCAL_AUTH_ENABLED",
        "CLERK_PUBLISHABLE_KEY",
        "CLERK_JWT_TEMPLATE",
        "NEUROCADE_DOCKER_PLATFORM",
    ):
        env.pop(leaked_key, None)
    env.update(
        {
            "BUILD_IMAGE_ENV_LOG": str(log_file),
            "ENV_FILE": str(env_file),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "build_image.sh")], check=True, env=env)

    log_text = log_file.read_text(encoding="utf-8")
    assert "--build-arg" not in log_text
    assert "pk_from_env" not in log_text
    assert "--platform linux/amd64" in log_text


def test_install_script_rejects_missing_option_values() -> None:
    for option in ("--mode", "--llm-provider", "--image"):
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "install.sh"), option, "--no-start"],
            text=True,
            capture_output=True,
        )

        assert result.returncode == 2
        assert f"{option} requires a value." in result.stderr


def test_install_no_start_writes_env_from_temp_checkout(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)
    run_log = tmp_path / "install-run.log"

    env = os.environ.copy()
    for key in (
        "APP_BASE_URL",
        "APP_HTTP_BIND",
        "APP_HTTP_PORT",
        "DATABASE_URL",
        "HOST_DATA_DIR",
        "NEUROCADE_DB_DIR",
        "NEUROCADE_IMAGE",
    ):
        env.pop(key, None)
    env.update(
        {
            "INSTALL_RUN_LOG": str(run_log),
            "LLM_BACKEND_URL": "https://llm.example.test",
            "LLM_BACKEND_MODEL": "model-from-env",
        }
    )

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "install.sh"),
            "--mode",
            "local",
            "--llm-provider",
            "openai-compatible",
            "--no-start",
            "--yes",
        ],
        check=True,
        cwd=checkout,
        env=env,
    )

    env_text = (checkout / ".env").read_text(encoding="utf-8")
    assert "APP_BASE_URL=http://localhost:8000" in env_text
    assert "DATABASE_URL=sqlite+pysqlite:///" in env_text
    assert f"HOST_DATA_DIR={checkout / 'neurocade-data'}" in env_text
    assert f"NEUROCADE_DB_DIR={checkout / 'neurocade-data'}" in env_text
    assert "LOCAL_AUTH_NAME=Local User" in env_text
    assert 'LOCAL_AUTH_NAME="Local User"' not in env_text
    assert "NEUROCADE_HOST_DATA_DIR" not in env_text
    assert "NEUROCADE_CONTAINER_DATABASE_URL" not in env_text
    assert "NEUROCADE_IMAGE=ghcr.io/deep-mi/neurocade:latest" in env_text
    assert "NEUROCADE_DOCKER_PLATFORM=" in env_text
    assert "LLM_BACKEND_URL=https://llm.example.test" in env_text
    assert "LLM_BACKEND_MODEL=model-from-env" in env_text
    assert run_log.read_text(encoding="utf-8").splitlines() == ["prepare-tools"]


def test_unattended_install_infers_explicit_provider_configuration(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)
    env = os.environ.copy()
    for key in (
        "LLM_PROVIDER_DEFAULT",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        env.pop(key, None)
    env["LLM_BACKEND_URL"] = "https://llm.example.test"

    subprocess.run(
        ["bash", str(scripts_dir / "install.sh"), "--mode", "local", "--no-start", "--yes"],
        check=True,
        cwd=checkout,
        env=env,
    )

    env_text = (checkout / ".env").read_text(encoding="utf-8")
    assert "LLM_PROVIDER_DEFAULT=openai-compatible" in env_text
    assert "LLM_BACKEND_URL=https://llm.example.test" in env_text


def test_unattended_install_without_provider_configuration_uses_no_llm(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)
    env = os.environ.copy()
    for key in (
        "LLM_PROVIDER_DEFAULT",
        "LLM_BACKEND_URL",
        "LLM_BACKEND_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        env.pop(key, None)

    subprocess.run(
        ["bash", str(scripts_dir / "install.sh"), "--mode", "local", "--no-start", "--yes"],
        check=True,
        cwd=checkout,
        env=env,
    )

    env_text = (checkout / ".env").read_text(encoding="utf-8")
    assert "LLM_PROVIDER_DEFAULT=no-llm" in env_text
    assert "LLM_BACKEND_URL=" in env_text


def test_unattended_install_rejects_explicit_openai_provider_without_url(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)
    env = os.environ.copy()
    for key in ("LLM_BACKEND_URL", "LLM_BACKEND_API_KEY"):
        env.pop(key, None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "install.sh"),
            "--mode",
            "local",
            "--llm-provider",
            "openai-compatible",
            "--no-start",
            "--yes",
        ],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "OpenAI-compatible base URL is required" in result.stderr
    assert not (checkout / ".env").exists()


def test_install_hides_existing_secret_in_interactive_prompt(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)

    existing_key = "existing-secret-api-key"
    (checkout / ".env").write_text(
        "\n".join(
            [
                "LLM_BACKEND_URL=https://llm.example.test",
                f"LLM_BACKEND_API_KEY={existing_key}",
                "LLM_BACKEND_MODEL=model-from-env",
            ]
        ),
        encoding="utf-8",
    )

    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            [
                "bash",
                str(scripts_dir / "install.sh"),
                "--mode",
                "local",
                "--llm-provider",
                "openai-compatible",
                "--no-start",
            ],
            cwd=checkout,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        os.write(master_fd, b"\n" * 8)
        assert process.wait(timeout=10) == 0

        output_chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output_chunks.append(chunk)
        output = b"".join(output_chunks).decode(errors="replace")
    finally:
        os.close(master_fd)
        if slave_fd >= 0:
            os.close(slave_fd)

    assert "OpenAI-compatible API key (optional) [**existing key**]:" in output
    assert existing_key not in output
    assert f"LLM_BACKEND_API_KEY={existing_key}" in (checkout / ".env").read_text(encoding="utf-8")


def test_install_can_pin_an_exact_published_image(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)

    image = "ghcr.io/deep-mi/neurocade:v2026.7.23-beta.1"
    subprocess.run(
        [
            "bash",
            str(scripts_dir / "install.sh"),
            "--mode",
            "local",
            "--llm-provider",
            "no-llm",
            "--image",
            image,
            "--no-start",
            "--yes",
        ],
        check=True,
        cwd=checkout,
    )

    assert f"NEUROCADE_IMAGE={image}" in (checkout / ".env").read_text(encoding="utf-8")


def test_demo_install_binds_to_all_interfaces(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)

    env = os.environ.copy()
    for key in ("APP_BASE_URL", "APP_HTTP_BIND", "APP_HTTP_PORT", "HOST_DATA_DIR"):
        env.pop(key, None)
    env.update(
        {
            "CLERK_PUBLISHABLE_KEY": "pk_test",
            "CLERK_SECRET_KEY": "sk_test",
            "CLERK_JWKS_URL": "https://clerk.example.test/.well-known/jwks.json",
            "CLERK_ISSUER": "https://clerk.example.test",
            "CLERK_AUDIENCE": "neurocade",
            "CLERK_JWT_TEMPLATE": "neurocade",
        }
    )
    subprocess.run(
        [
            "bash",
            str(scripts_dir / "install.sh"),
            "--mode",
            "demo",
            "--llm-provider",
            "no-llm",
            "--no-start",
            "--yes",
        ],
        check=True,
        cwd=checkout,
        env=env,
    )

    assert "APP_HTTP_BIND=0.0.0.0" in (checkout / ".env").read_text(encoding="utf-8")


def test_install_script_does_not_manage_freesurfer_license() -> None:
    install_script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "FreeSurfer license" not in install_script
    assert "FREESURFER_LICENSE" not in install_script


def test_install_defaults_apple_silicon_to_amd64_emulation(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = _write_installer_checkout(checkout)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uname = bin_dir / "uname"
    uname.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  -s) printf 'Darwin\\n' ;;\n"
        "  -m) printf 'arm64\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    subprocess.run(
        [
            "bash",
            str(scripts_dir / "install.sh"),
            "--mode",
            "local",
            "--llm-provider",
            "no-llm",
            "--no-start",
            "--yes",
        ],
        check=True,
        cwd=checkout,
        env=env,
    )

    assert "NEUROCADE_DOCKER_PLATFORM=linux/amd64" in (checkout / ".env").read_text(encoding="utf-8")


def test_container_launcher_uses_mounted_sqlite_database() -> None:
    run_script = (REPO_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")

    assert "sqlite+pysqlite:////database/neurocade.db" in run_script
    assert 'NEUROCADE_DB_DIR="${NEUROCADE_DB_DIR:-$HOST_DATA_DIR}"' in run_script
    assert '"${NEUROCADE_DB_DIR}:/database"' in run_script
    assert "-e HOST_DATA_DIR=/data" in run_script
    assert "NEUROCADE_CONTAINER_DATABASE_URL" not in run_script
    assert "NEUROCADE_SAMPLE_CASE_URL" in run_script
    assert "NEUROCADE_SAMPLE_CASE_SHA256" in run_script
    assert "NEUROCADE_SKIP_SAMPLE_CASE" in run_script


def test_docker_launcher_mounts_configured_database_directory(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    host_data_dir = tmp_path / "data"
    db_dir = tmp_path / "database"
    env_file = checkout / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"HOST_DATA_DIR={host_data_dir}",
                f"NEUROCADE_DB_DIR={db_dir}",
                "NEUROCADE_IMAGE=neurocade:test",
                "NEUROCADE_SKIP_SAMPLE_CASE=true",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    subprocess.run(
        ["bash", str(scripts_dir / "run.sh"), "start"],
        check=True,
        cwd=checkout,
        env=_launcher_env(env_file, bin_dir, log_file),
    )

    log_text = log_file.read_text(encoding="utf-8")
    assert f"-v {host_data_dir}:/data" in log_text
    assert f"-v {db_dir}:/database" in log_text
    assert "-e DATABASE_URL=sqlite+pysqlite:////database/neurocade.db" in log_text


def test_docker_launcher_downloads_missing_sample_case_with_app_image(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    env_file = checkout / ".env"
    host_data_dir = tmp_path / "data"
    env_file.write_text(
        "\n".join(
            [
                f"HOST_DATA_DIR={host_data_dir}",
                "NEUROCADE_IMAGE=neurocade:test",
                "NEUROCADE_SAMPLE_CASE_URL=https://example.test/sample.tar.gz",
            ]
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    env = _launcher_env(env_file, bin_dir, log_file)

    subprocess.run(["bash", str(scripts_dir / "run.sh"), "start"], check=True, env=env, cwd=checkout)

    log_text = log_file.read_text(encoding="utf-8")
    assert "-e SAMPLE_CASE_URL=https://example.test/sample.tar.gz" in log_text
    assert "-e SAMPLE_CASE_SHA256=" in log_text
    assert f"-v {checkout / 'sample_case'}:/sample_case" in log_text
    assert "python -m api_service.runtime_tools.prepare_sample_case" in log_text


def test_docker_launcher_forwards_configured_platform_to_every_container(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    host_data_dir = tmp_path / "data"
    (checkout / ".env").write_text(
        "\n".join(
            [
                f"HOST_DATA_DIR={host_data_dir}",
                "NEUROCADE_IMAGE=neurocade:test",
                "NEUROCADE_DOCKER_PLATFORM=linux/amd64",
                "NEUROCADE_SAMPLE_CASE_URL=https://example.test/sample.tar.gz",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    env = _launcher_env(checkout / ".env", bin_dir, log_file)
    subprocess.run(["bash", str(scripts_dir / "run.sh"), "start"], check=True, env=env, cwd=checkout)

    log_text = log_file.read_text(encoding="utf-8")
    assert log_text.count("--platform linux/amd64") == 6


def test_docker_launcher_pulls_mismatched_platform_image(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    host_data_dir = tmp_path / "data"
    (checkout / ".env").write_text(
        "\n".join(
            [
                f"HOST_DATA_DIR={host_data_dir}",
                "NEUROCADE_IMAGE=neurocade:test",
                "NEUROCADE_DOCKER_PLATFORM=linux/amd64",
                "NEUROCADE_SKIP_SAMPLE_CASE=true",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    env = _launcher_env(
        checkout / ".env",
        bin_dir,
        log_file,
        FAKE_DOCKER_IMAGE_PLATFORM="linux/arm64",
    )
    subprocess.run(["bash", str(scripts_dir / "run.sh"), "start"], check=True, env=env, cwd=checkout)

    log_text = log_file.read_text(encoding="utf-8")
    assert "pull --platform linux/amd64 neurocade:test" in log_text
    assert "run --name neurocade --platform linux/amd64" in log_text


def test_docker_launcher_pulls_missing_published_image(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "build_image.sh", scripts_dir / "build_image.sh")
    shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")

    host_data_dir = tmp_path / "data"
    (checkout / ".env").write_text(
        "\n".join(
            [
                f"HOST_DATA_DIR={host_data_dir}",
                "NEUROCADE_IMAGE=ghcr.io/deep-mi/neurocade:test",
                "NEUROCADE_SKIP_SAMPLE_CASE=true",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    env = _launcher_env(
        checkout / ".env",
        bin_dir,
        log_file,
        FAKE_DOCKER_IMAGE_PRESENT="false",
    )
    subprocess.run(["bash", str(scripts_dir / "run.sh"), "start"], check=True, env=env, cwd=checkout)

    log_text = log_file.read_text(encoding="utf-8")
    assert "pull ghcr.io/deep-mi/neurocade:test" in log_text
    assert "build" not in log_text


def test_docker_launcher_does_not_manage_freesurfer_license(tmp_path) -> None:
    env_file = tmp_path / ".env"
    host_data_dir = tmp_path / "data"
    license_file = tmp_path / "license.txt"
    license_file.write_text("license\n", encoding="utf-8")
    env_file.write_text(
        "\n".join(
            [
                f"HOST_DATA_DIR={host_data_dir}",
                f"FREESURFER_LICENSE={license_file}",
                "NEUROCADE_IMAGE=neurocade:test",
            ]
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    env = _launcher_env(env_file, bin_dir, log_file)

    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "run.sh"), "start"], check=True, env=env)

    log_text = log_file.read_text(encoding="utf-8")
    assert "/fs_license.txt" not in log_text
    assert "FREESURFER_LICENSE" not in log_text


def test_env_example_documents_apptainer_runtime() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "NEUROCADE_SIF_DIR=" in env_example
    assert "NEUROCADE_RUNTIME_BACKEND" not in env_example
    assert "NEUROCADE_IMAGE=ghcr.io/deep-mi/neurocade:latest" in env_example
    assert "NEUROCADE_DOCKER_PLATFORM=" in env_example
    assert "NEUROCADE_DB_DIR=" in env_example
    removed_catalog_env = "NEUROCADE_" + "INSTALLED_TOOLS_JSONL"
    for term in (
        "RUNTIME_RUNNER_TOKEN=",
        "RUNTIME_RUNNER_URL=",
        "REDIS_URL=",
        "NEUROCADE_HOST_DATA_DIR=",
        "NEUROCADE_CONTAINER_DATABASE_URL=",
        "APP_DOMAIN=",
        "ACME_EMAIL=",
        removed_catalog_env,
    ):
        assert term not in env_example


def test_release_workflow_matches_the_docker_monolith() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "scripts/apptainer/images.sh" not in workflow
    assert "Install Apptainer" not in workflow
    assert "SKIP_CONTAINER_ARTIFACTS" not in workflow
    assert "Postgres, Redis, and Traefik SIFs" not in workflow
    for obsolete_term in (
        "actions/setup-node",
        "SAMPLE_CASE_ARTIFACT_URL",
        "SKIP_SAMPLE_CASE_ARTIFACT",
        "build_artifacts.sh",
        "stage_upload_assets.sh",
        "dist/release-upload",
    ):
        assert obsolete_term not in workflow
    assert 'gh release create "${args[@]}"' in workflow
    assert "packages: write" in workflow
    assert "docker/build-push-action@v7" in workflow
    assert "platforms: linux/amd64" in workflow
    assert "NEUROCADE_VERSION=${{ steps.release.outputs.version }}" in workflow
    assert 'printenv NEUROCADE_BUILD_VERSION' in workflow
    assert "ghcr.io/deep-mi/neurocade:${{ steps.release.outputs.tag }}" in workflow
    assert "Verify anonymous image access" in workflow
    assert 'DOCKER_CONFIG="$anonymous_config" docker pull' in workflow
    assert "docker logout ghcr.io" not in workflow
    assert "Smoke-test published image" in workflow
    assert "/api/app/healthz" in workflow
    assert "/api/app/frontend-config" in workflow
    assert workflow.index("Smoke-test published image") < workflow.index("Publish channel tag")
    assert 'docker buildx imagetools create --tag "$CHANNEL_IMAGE" "$RELEASE_IMAGE"' in workflow


def test_dockerfile_verifies_apptainer_and_uses_locked_dependencies() -> None:
    dockerfile = (REPO_ROOT / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")

    assert "2723b2928cfc30edf687723c49556ec4e013f0bf7cdb43a5a76bca7bd3c70792" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "pip uninstall -y uv" in dockerfile
    assert "ARG NEUROCADE_VERSION=0.0.0" in dockerfile
    assert 'NEUROCADE_BUILD_VERSION="$NEUROCADE_VERSION"' in dockerfile
    assert "npm install --global npm@11.10.0" in dockerfile
