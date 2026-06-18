"""Generate a pinned core Docker runtime tool catalog for Compose installs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .container_specs import CORE_SPECS


def catalog_dir() -> Path:
    configured = os.environ.get("TOOL_CATALOG_DIR")
    if configured:
        return Path(configured).expanduser()
    installed_tools = os.environ.get("NEUROCADE_INSTALLED_TOOLS_JSONL")
    if installed_tools:
        return Path(installed_tools).expanduser().parent
    return Path("llm-data/tool-catalog")


def generate_core_docker_catalog(target_dir: Path | None = None) -> tuple[Path, Path]:
    """Write installed container and tool indexes for pinned Docker core images."""
    root = target_dir or catalog_dir()
    root.mkdir(parents=True, exist_ok=True)
    containers_path = Path(os.environ.get("NEUROCADE_CONTAINER_INVENTORY") or root / "installed_containers.json")
    tools_path = Path(os.environ.get("NEUROCADE_INSTALLED_TOOLS_JSONL") or root / "installed_tools.jsonl")
    containers_path.parent.mkdir(parents=True, exist_ok=True)
    tools_path.parent.mkdir(parents=True, exist_ok=True)

    containers: list[dict[str, object]] = []
    tool_rows: list[dict[str, object]] = []
    for name, spec in CORE_SPECS.items():
        docker_uri = spec.docker_uri
        if name == "bash_image":
            docker_uri = os.environ.get("NEUROCADE_BASH_IMAGE", "neurocade-runtime-bash:local")
        if not docker_uri:
            continue
        image = docker_uri.removeprefix("docker://")
        container_row = {
            "name": name,
            "kind": spec.kind,
            "app": spec.app,
            "runtime_version": spec.runtime_version,
            "build_date": spec.build_date,
            "image_path": image,
            "docker_uri": image,
        }
        containers.append(container_row)
        for command_name in spec.command_names:
            tool_rows.append(
                {
                    "name": command_name,
                    "aliases": [],
                    "toolbox": spec.name,
                    "app": spec.app,
                    "runtime_version": spec.runtime_version,
                    "build_date": spec.build_date,
                    "image_path": image,
                    "docker_uri": image,
                    "container_command": command_name,
                    "requires_license": spec.requires_license,
                }
            )

    containers_path.write_text(json.dumps({"containers": containers}, indent=2) + "\n", encoding="utf-8")
    tools_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in tool_rows), encoding="utf-8")
    return containers_path, tools_path


def main() -> None:
    containers_path, tools_path = generate_core_docker_catalog()
    print(f"Wrote Docker core container inventory: {containers_path}", flush=True)
    print(f"Wrote Docker core tool catalog: {tools_path}", flush=True)


if __name__ == "__main__":
    main()
