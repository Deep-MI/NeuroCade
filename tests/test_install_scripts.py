"""Test Docker-only install and launcher scripts."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_fake_docker(bin_dir: Path, log_file: Path) -> Path:
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_RUN_LOG"\n'
        'case "$1:$2" in\n'
        "  image:inspect) exit 0 ;;\n"
        "  ps:*) exit 0 ;;\n"
        "  rm:-f) exit 0 ;;\n"
        "  run:*) exit 0 ;;\n"
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return docker


def test_shell_entrypoints_parse() -> None:
    scripts = [
        "scripts/install.sh",
        "scripts/lib/env.sh",
        "scripts/build_image.sh",
        "scripts/run.sh",
        "scripts/desktop/run.sh",
        "scripts/admin/reset_app_state.sh",
        "scripts/release/build_artifacts.sh",
        "scripts/release/stage_upload_assets.sh",
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

    # The monolith has no runtime-runner sidecar, Postgres, or Redis to configure.
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


def test_build_image_loads_frontend_auth_env(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LOCAL_AUTH_ENABLED=false",
                'VITE_CLERK_PUBLISHABLE_KEY="pk_from_env"',
                "VITE_CLERK_JWT_TEMPLATE=neurocade-template",
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
    # build_image.sh lets an already-set environment variable win over the .env
    # file, and importing api_service modules calls load_dotenv() which leaks the
    # repo .env into os.environ. Scrub the keys under test so the temp .env is the
    # only source, keeping this test independent of suite ordering.
    for leaked_key in (
        "VITE_API_URL",
        "LOCAL_AUTH_ENABLED",
        "VITE_LOCAL_AUTH_ENABLED",
        "VITE_CLERK_PUBLISHABLE_KEY",
        "VITE_CLERK_JWT_TEMPLATE",
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
    assert "--build-arg NC_VITE_API_URL=/api/app" in log_text
    assert "--build-arg NC_LOCAL_LOGIN=false" in log_text
    assert "--build-arg NC_CLERK_PUBLIC=pk_from_env" in log_text
    assert "--build-arg NC_CLERK_TEMPLATE=neurocade-template" in log_text


def test_install_script_rejects_missing_option_values() -> None:
    for option in ("--mode", "--llm-provider"):
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "install.sh"), option, "--no-start"],
            text=True,
            capture_output=True,
        )

        assert result.returncode == 2
        assert f"{option} requires a value." in result.stderr


def test_install_no_start_writes_env_from_temp_checkout(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    scripts_dir = checkout / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "install.sh", scripts_dir / "install.sh")
    shutil.copy2(REPO_ROOT / "scripts" / "run.sh", scripts_dir / "run.sh")

    env = os.environ.copy()
    env.update(
        {
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
    assert "NEUROCADE_CONTAINER_DATABASE_URL=sqlite+pysqlite:////data/neurocade.db" in env_text
    assert "LLM_BACKEND_URL=https://llm.example.test" in env_text
    assert "LLM_BACKEND_MODEL=model-from-env" in env_text


def test_install_script_does_not_manage_freesurfer_license() -> None:
    install_script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "FreeSurfer license" not in install_script
    assert "FREESURFER_LICENSE" not in install_script


def test_container_launcher_uses_container_database_url() -> None:
    run_script = (REPO_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")

    assert 'CONTAINER_DATABASE_URL="${NEUROCADE_CONTAINER_DATABASE_URL:-sqlite+pysqlite:////data/neurocade.db}"' in run_script
    assert "sqlite+pysqlite:////data/neurocade.db" in run_script
    assert "-e HOST_DATA_DIR=/data" in run_script
    assert "NEUROCADE_SAMPLE_CASE_URL" in run_script
    assert "NEUROCADE_SKIP_SAMPLE_CASE" in run_script


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
                f"NEUROCADE_HOST_DATA_DIR={host_data_dir}",
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

    env = os.environ.copy()
    env.update(
        {
            "DOCKER_RUN_LOG": str(log_file),
            "ENV_FILE": str(env_file),
            "NEUROCADE_HOST_DATA_DIR": str(host_data_dir),
            "NEUROCADE_IMAGE": "neurocade:test",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    subprocess.run(["bash", str(scripts_dir / "run.sh"), "start"], check=True, env=env, cwd=checkout)

    log_text = log_file.read_text(encoding="utf-8")
    assert "-e SAMPLE_CASE_URL=https://example.test/sample.tar.gz" in log_text
    assert f"-v {checkout / 'sample_case'}:/sample_case" in log_text
    assert "python -c" in log_text


def test_docker_launcher_does_not_manage_freesurfer_license(tmp_path) -> None:
    env_file = tmp_path / ".env"
    host_data_dir = tmp_path / "data"
    license_file = tmp_path / "license.txt"
    license_file.write_text("license\n", encoding="utf-8")
    env_file.write_text(
        "\n".join(
            [
                f"NEUROCADE_HOST_DATA_DIR={host_data_dir}",
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

    env = os.environ.copy()
    env.update(
        {
            "DOCKER_RUN_LOG": str(log_file),
            "ENV_FILE": str(env_file),
            "NEUROCADE_HOST_DATA_DIR": str(host_data_dir),
            "FREESURFER_LICENSE": str(license_file),
            "NEUROCADE_IMAGE": "neurocade:test",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "run.sh"), "start"], check=True, env=env)

    log_text = log_file.read_text(encoding="utf-8")
    assert "/fs_license.txt" not in log_text
    assert "FREESURFER_LICENSE" not in log_text


def test_env_example_documents_runtime_backend() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    # Apptainer is the default tool runtime; the runtime-runner sidecar is gone.
    assert "NEUROCADE_RUNTIME_BACKEND=" in env_example
    removed_catalog_env = "NEUROCADE_" + "INSTALLED_TOOLS_JSONL"
    for term in ("RUNTIME_RUNNER_TOKEN=", "RUNTIME_RUNNER_URL=", "REDIS_URL=", removed_catalog_env):
        assert term not in env_example
