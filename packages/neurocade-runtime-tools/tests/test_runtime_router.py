"""Test runtime router behavior for NeuroCade."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from neurocade_runtime_tools.runtime_router import (
    RouterError,
    build_runtime_command,
    ensure_image_exists,
    install_shims,
    resolve_tool,
)
from neurocade_runtime_tools.apptainer_command import RuntimeBind


def _write_tools(path: Path) -> Path:
    """Write a minimal runtime tool index for router tests."""
    image = path.parent / "containers" / "fastsurfer_2.4.2_20260115.simg"
    rows = [
        {
            "name": "run_fastsurfer.sh",
            "aliases": ["run_fastsurfer"],
            "toolbox": "fastsurfer",
            "app": "fastsurfer",
            "runtime_version": "2.4.2",
            "build_date": "20260115",
            "image_name": "fastsurfer_2.4.2_20260115.simg",
            "image_path": str(image),
            "container_command": "/fastsurfer/run_fastsurfer.sh",
            "source_path": "sources/neurocontainers/releases/fastsurfer/2.4.2.json",
            "synopsis": "run_fastsurfer.sh",
        },
        {
            "name": "freeview",
            "aliases": [],
            "toolbox": "freesurfer",
            "app": "freeviewGUI-freesurfer",
            "runtime_version": "8.2.0",
            "build_date": "20260331",
            "image_name": "freeviewGUI-freesurfer_8.2.0_20260331.simg",
            "image_path": str(path.parent / "containers" / "freeview.simg"),
            "container_command": "freeview",
            "source_path": "sources/neurocontainers/releases/freesurfer/8.2.0.json",
            "synopsis": "freeview",
        },
        {
            "name": "mri_synthseg",
            "aliases": [],
            "toolbox": "freesurfer",
            "app": "freesurfer",
            "runtime_version": "8.1.0",
            "build_date": "latest",
            "image_name": "freesurfer_8.1.0_latest.simg",
            "image_path": str(path.parent / "containers" / "freesurfer.simg"),
            "container_command": "mri_synthseg",
            "source_path": "generated:neurocade-containers",
            "synopsis": "usage: mri_synthseg [-h] [--i I] [--o O]",
            "description": "SynthSeg segmentation.",
            "searchable_text": "mri_synthseg SynthSeg segmentation",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_resolve_tool_uses_aliases(tmp_path: Path):
    tools = _write_tools(tmp_path / "tools.jsonl")
    row = resolve_tool("run_fastsurfer", records_jsonl=tools)
    assert row["name"] == "run_fastsurfer.sh"
    assert row["container_command"] == "/fastsurfer/run_fastsurfer.sh"


def test_resolve_tool_not_found_reports_close_matches(tmp_path: Path):
    tools = _write_tools(tmp_path / "tools.jsonl")

    try:
        resolve_tool("synthseg", records_jsonl=tools)
    except RouterError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing tool should fail clearly")

    assert "Tool 'synthseg' was not found" in message
    assert "exact installed catalog name" in message
    assert "mri_synthseg" in message
    assert "tool_search" in message


def test_build_runtime_command_uses_absolute_container_command(tmp_path: Path):
    tools = _write_tools(tmp_path / "tools.jsonl")
    cmd = build_runtime_command(
        "run_fastsurfer.sh",
        ["--help"],
        records_jsonl=tools,
        project_root=tmp_path,
    )

    assert Path(cmd[0]).name == "apptainer"
    assert cmd[1:] == [
        "exec",
        "--net",
        "--network",
        "none",
        "--cleanenv",
        "--no-home",
        "--pwd",
        str(tmp_path),
        str(tmp_path / "containers" / "fastsurfer_2.4.2_20260115.simg"),
        "/fastsurfer/run_fastsurfer.sh",
        "--help",
    ]


def test_runtime_command_ignores_neurodesk_singularity_opts(monkeypatch, tmp_path: Path):
    """Ignore unsafe Neurodesk bind options supplied through the environment."""
    tools = _write_tools(tmp_path / "tools.jsonl")
    monkeypatch.setenv("neurodesk_singularity_opts", "--bind /etc:/host:rw --net")

    cmd = build_runtime_command(
        "run_fastsurfer.sh",
        ["--help"],
        records_jsonl=tools,
        project_root=tmp_path,
    )

    assert "/etc:/host:rw" not in cmd
    assert cmd.count("--net") == 1


def test_build_runtime_command_accepts_structured_binds(tmp_path: Path):
    """Translate structured bind requests into Apptainer arguments."""
    tools = _write_tools(tmp_path / "tools.jsonl")
    case_dir = tmp_path / "case"
    output_dir = tmp_path / "output"
    case_dir.mkdir()
    output_dir.mkdir()

    cmd = build_runtime_command(
        "run_fastsurfer.sh",
        ["--help"],
        records_jsonl=tools,
        project_root=tmp_path,
        binds=[
            RuntimeBind(case_dir, "/case", "ro"),
            RuntimeBind(output_dir, "/output", "rw"),
        ],
    )

    assert "--bind" in cmd
    assert f"{case_dir.resolve()}:/case:ro" in cmd
    assert f"{output_dir.resolve()}:/output:rw" in cmd
    assert cmd[1:4] == ["exec", "--net", "--network"]


def test_workspace_bind_disables_implicit_cwd_mount(tmp_path: Path):
    tools = _write_tools(tmp_path / "tools.jsonl")
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    cmd = build_runtime_command(
        "freeview",
        ["--help"],
        records_jsonl=tools,
        project_root=tmp_path,
        binds=[RuntimeBind(workspace_dir, "/workspace", "rw")],
    )

    assert "--no-mount" in cmd
    assert "cwd" in cmd
    assert "--pwd" in cmd
    pwd_index = cmd.index("--pwd")
    assert cmd[pwd_index + 1] == "/workspace"
    assert "none" in cmd
    assert cmd[cmd.index("--no-mount") : cmd.index("--no-mount") + 2] == ["--no-mount", "cwd"]
    assert cmd[cmd.index("--pwd") : cmd.index("--pwd") + 2] == ["--pwd", "/workspace"]


def test_missing_image_reports_install_error(tmp_path: Path):
    """Report the install command when a referenced runtime image is absent."""
    tools = _write_tools(tmp_path / "tools.jsonl")
    row = resolve_tool("run_fastsurfer.sh", records_jsonl=tools)

    try:
        ensure_image_exists(row)
    except RouterError as exc:
        assert "FastSurfer container is not installed" in str(exc)
        assert "scripts/containers.sh" in str(exc)
    else:
        raise AssertionError("missing image should fail clearly")


def test_install_shims_creates_tool_entrypoints(tmp_path: Path):
    tools = _write_tools(tmp_path / "tools.jsonl")
    created = install_shims(records_jsonl=tools, bin_dir=tmp_path / "bin")
    names = {path.name for path in created}
    assert {"run_fastsurfer.sh", "run_fastsurfer", "freeview"}.issubset(names)
    assert "exec python3" in (tmp_path / "bin" / "freeview").read_text(encoding="utf-8")


def test_install_shims_skips_shell_metacharacter_names(tmp_path: Path):
    tools = tmp_path / "tools.jsonl"
    image = tmp_path / "container.sif"
    rows = [
        {
            "name": "safe_tool",
            "aliases": ["unsafe;touch-pwned", "$(unsafe)"],
            "toolbox": "toolbox",
            "app": "app",
            "runtime_version": "1",
            "build_date": "20260101",
            "image_name": "container.sif",
            "image_path": str(image),
            "container_command": "safe_tool",
        },
        {
            "name": "bad;touch-pwned",
            "aliases": [],
            "toolbox": "toolbox",
            "app": "app",
            "runtime_version": "1",
            "build_date": "20260101",
            "image_name": "container.sif",
            "image_path": str(image),
            "container_command": "safe_tool",
        },
    ]
    tools.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    created = install_shims(records_jsonl=tools, bin_dir=tmp_path / "bin")
    names = {path.name for path in created}

    assert names == {"safe_tool"}


def test_run_parser_accepts_records_jsonl_before_tool():
    """Accept router options before the tool name in the run subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--records-jsonl", type=Path)
    run_parser.add_argument("tool")
    run_parser.add_argument("args", nargs=argparse.REMAINDER)

    parsed = parser.parse_args(
        ["run", "--records-jsonl", "tools.jsonl", "freeview", "--", "--help"]
    )
    assert parsed.command == "run"
    assert parsed.records_jsonl == Path("tools.jsonl")
    assert parsed.tool == "freeview"
    assert parsed.args == ["--help"]
