"""Test Docker-only install and launcher scripts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shell_entrypoints_parse() -> None:
    scripts = [
        "scripts/install.sh",
        "scripts/install/common.sh",
        "scripts/install/env.sh",
        "scripts/install/doctor.sh",
        "scripts/install/node.sh",
        "scripts/install/python.sh",
        "scripts/lib/env.sh",
        "scripts/build_image.sh",
        "scripts/build_runtime_tools.sh",
        "scripts/compose/lib.sh",
        "scripts/compose/up.sh",
        "scripts/compose/down.sh",
        "scripts/compose/images.sh",
        "scripts/compose/logs.sh",
        "scripts/compose/status.sh",
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
            REPO_ROOT / "scripts" / "install" / "common.sh",
            REPO_ROOT / "scripts" / "install" / "env.sh",
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


def test_compose_launcher_uses_configurable_port_and_project_name() -> None:
    compose_lib = (REPO_ROOT / "scripts" / "compose" / "lib.sh").read_text(encoding="utf-8")
    compose_up = (REPO_ROOT / "scripts" / "compose" / "up.sh").read_text(encoding="utf-8")
    compose_images = (REPO_ROOT / "scripts" / "compose" / "images.sh").read_text(encoding="utf-8")
    build_runtime_tools = (REPO_ROOT / "scripts" / "build_runtime_tools.sh").read_text(encoding="utf-8")

    assert "APP_HTTP_PORT" in compose_lib
    assert "neurocade" in compose_lib
    assert "docker compose" in compose_lib
    assert "compose up" in compose_up
    assert "scripts/build_image.sh" in compose_up
    assert "scripts/build_image.sh" in compose_images
    assert "docker_catalog" in build_runtime_tools
    assert "apptainer build" in build_runtime_tools


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
    npm = bin_dir / "npm"
    npm.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                (
                    'printf "npm:%s VITE_API_URL=%s VITE_LOCAL_AUTH_ENABLED=%s '
                    'VITE_CLERK_PUBLISHABLE_KEY=%s VITE_CLERK_JWT_TEMPLATE=%s\\n" '
                    '"$*" "$VITE_API_URL" "$VITE_LOCAL_AUTH_ENABLED" '
                    '"$VITE_CLERK_PUBLISHABLE_KEY" "$VITE_CLERK_JWT_TEMPLATE" '
                    '>> "$BUILD_IMAGE_ENV_LOG"'
                ),
            ]
        ),
        encoding="utf-8",
    )
    npm.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
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
            "NEUROCADE_BUILD_RUNTIME_TOOLS": "0",
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    subprocess.run(["bash", str(REPO_ROOT / "scripts" / "build_image.sh")], check=True, env=env)

    log_text = log_file.read_text(encoding="utf-8")
    assert "VITE_API_URL=/api/app" in log_text
    assert "VITE_LOCAL_AUTH_ENABLED=false" in log_text
    assert "VITE_CLERK_PUBLISHABLE_KEY=pk_from_env" in log_text
    assert "VITE_CLERK_JWT_TEMPLATE=neurocade-template" in log_text


def test_install_env_uses_host_paths_for_native_runtime() -> None:
    env_script = (REPO_ROOT / "scripts" / "install" / "env.sh").read_text(encoding="utf-8")

    assert 'env_line HOST_DATA_DIR "$host_data_dir"' in env_script
    assert 'env_line_configured "$root" DATABASE_URL "sqlite+pysqlite:///$host_data_dir/neurocade.db"' in env_script
    assert 'env_line_configured "$root" NEUROCADE_SIF_DIR "$host_data_dir/sif"' in env_script


def test_container_launchers_rewrite_host_sqlite_urls() -> None:
    compose_lib = (REPO_ROOT / "scripts" / "compose" / "lib.sh").read_text(encoding="utf-8")
    run_container = (REPO_ROOT / "scripts" / "run_container.sh").read_text(encoding="utf-8")
    compose_yaml = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "NEUROCADE_CONTAINER_DATABASE_URL" in compose_lib
    assert '"$DATABASE_URL" == sqlite:*' in compose_lib
    assert "NEUROCADE_CONTAINER_DATABASE_URL" in compose_yaml
    assert "DATABASE_URL: ${DATABASE_URL" not in compose_yaml
    assert "CONTAINER_DATABASE_URL" in run_container
    assert '"$DATABASE_URL" == sqlite:*' in run_container


def test_env_example_documents_runtime_backend() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    # Apptainer is the default tool runtime; the runtime-runner sidecar is gone.
    assert "NEUROCADE_RUNTIME_BACKEND=" in env_example
    for term in ("RUNTIME_RUNNER_TOKEN=", "RUNTIME_RUNNER_URL=", "REDIS_URL="):
        assert term not in env_example
