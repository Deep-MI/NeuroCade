"""Test Docker-only install and launcher scripts."""

from __future__ import annotations

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


def test_install_scripts_are_docker_only() -> None:
    script_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            REPO_ROOT / "scripts" / "install.sh",
            REPO_ROOT / "scripts" / "install" / "common.sh",
            REPO_ROOT / "scripts" / "install" / "env.sh",
            REPO_ROOT / "scripts" / "desktop" / "run.sh",
        ]
    )

    legacy_terms = [
        "scripts/" + "ap" + "ptainer",
        "runtime_" + "cache_env.sh",
        "HOST_" + "RUNTIME_RUNNER_",
        "NEUROCADE_" + "RUNTIME_BACKEND",
    ]
    for term in legacy_terms:
        assert term not in script_text


def test_compose_launcher_uses_configurable_port_and_project_name() -> None:
    compose_lib = (REPO_ROOT / "scripts" / "compose" / "lib.sh").read_text(encoding="utf-8")
    compose_up = (REPO_ROOT / "scripts" / "compose" / "up.sh").read_text(encoding="utf-8")

    assert "APP_HTTP_PORT" in compose_lib
    assert "neurocade" in compose_lib
    assert "docker compose" in compose_lib
    assert "compose up" in compose_up


def test_env_example_documents_docker_runtime() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "RUNTIME_RUNNER_TOKEN=" in env_example
    assert "RUNTIME_RUNNER_URL=" in env_example
    for term in ("NEUROCADE_" + "RUNTIME_BACKEND", "AP" + "PTAINER_"):
        assert term not in env_example
