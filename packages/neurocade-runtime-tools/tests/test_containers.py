"""Test neurocade containers behavior for NeuroCade."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from neurocade_runtime_tools import containers
from neurocade_runtime_tools.containers import (
    CORE_SPECS,
    container_status,
    core_install_plan,
    install_core,
    refresh_index,
    resolve_core_image,
)
from neurocade_runtime_tools.execution import RuntimeExecutionResult
from neurocade_runtime_tools.retrieval import hybrid_rank


@pytest.fixture(autouse=True)
def _skip_real_container_arch_validation(monkeypatch):
    """Keep unit tests on fake image files from invoking Apptainer."""
    monkeypatch.setattr(containers, "_validate_container_architecture", lambda _image: None)


def test_refresh_index_generates_inventory_and_installed_tools(tmp_path: Path, monkeypatch):
    """Verify refresh_index writes container inventory and tool indexes."""
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "core" / "fastsurfer_2.4.2_20260115" / "fastsurfer_2.4.2_20260115.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    refresh_index(root=root, harvest_help=False)

    inventory_path = root / "llm-data" / "tool-catalog" / "installed_containers.json"
    tools_path = root / "llm-data" / "tool-catalog" / "installed_tools.jsonl"
    container_tools_path = image.parent / "tool_index.jsonl"
    container_meta_path = image.parent / "index_meta.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    tool_rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sidecar_rows = [json.loads(line) for line in container_tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sidecar_meta = json.loads(container_meta_path.read_text(encoding="utf-8"))

    assert inventory["generated_at"]
    assert inventory["containers"][0]["name"] == "fastsurfer"
    assert inventory["containers"][0]["image_size_bytes"] > 0
    assert any(row["name"] == "mri_info" and row["image_path"] == str(image.resolve()) for row in tool_rows)
    assert sidecar_rows == tool_rows
    assert sidecar_meta["container"]["name"] == "fastsurfer"
    assert resolve_core_image("fastsurfer", root=root) == image.resolve()


def test_refresh_index_uses_prebuilt_core_index_without_discovery(tmp_path: Path, monkeypatch):
    """Verify known core containers install prebuilt indexes without command discovery."""
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    spec = CORE_SPECS["dcm2niix"]
    image = container_root / spec.kind / spec.directory_name / spec.image_name
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.setattr(
        containers,
        "discover_container_commands",
        lambda _container: (_ for _ in ()).throw(AssertionError("live discovery should not run")),
    )

    refresh_index(root=root, harvest_help=False)

    sidecar_rows = [json.loads(line) for line in (image.parent / "tool_index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    sidecar_meta = json.loads((image.parent / "index_meta.json").read_text(encoding="utf-8"))

    assert [row["name"] for row in sidecar_rows] == ["dcm2niix"]
    assert sidecar_rows[0]["image_path"] == str(image.resolve())
    assert "prebuilt_indexes/dcm2niix_v1.0.20240202_20260512/tool_index.jsonl" in sidecar_rows[0]["source_path"]
    assert sidecar_meta["image_path"] == str(image.resolve())
    assert sidecar_meta["image_size_bytes"] == image.stat().st_size
    assert sidecar_meta["image_mtime_ns"] == image.stat().st_mtime_ns
    assert sidecar_meta["prebuilt_index"].endswith("prebuilt_indexes/dcm2niix_v1.0.20240202_20260512")


def test_refresh_container_index_rebuild_forces_live_discovery(tmp_path: Path, monkeypatch):
    """Verify rebuild mode bypasses matching prebuilt indexes."""
    spec = CORE_SPECS["dcm2niix"]
    image = tmp_path / spec.image_name
    image.write_text("fake image", encoding="utf-8")
    container = containers._container_row(spec, image)
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setattr(containers, "discover_container_commands", lambda _container: ["live_tool"])

    refreshed = containers.refresh_container_index(container, harvest_help=False, use_prebuilt_index=False)
    sidecar_rows = [json.loads(line) for line in (image.parent / "tool_index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    sidecar_meta = json.loads((image.parent / "index_meta.json").read_text(encoding="utf-8"))

    assert refreshed["commands"] == ["live_tool"]
    assert [row["name"] for row in sidecar_rows] == ["live_tool"]
    assert "prebuilt_index" not in sidecar_meta


def test_refresh_container_index_ignores_mismatched_prebuilt_identity(tmp_path: Path, monkeypatch):
    """Verify stale prebuilt index identity falls back to live discovery."""
    spec = CORE_SPECS["dcm2niix"]
    image = tmp_path / spec.image_name
    image.write_text("fake image", encoding="utf-8")
    prebuilt_root = tmp_path / "prebuilt"
    index_dir = prebuilt_root / spec.directory_name
    index_dir.mkdir(parents=True)
    (index_dir / "identity.json").write_text(
        json.dumps(
            {
                "name": spec.name,
                "app": spec.app,
                "runtime_version": "older",
                "build_date": spec.build_date,
                "image_name": spec.image_name,
            }
        ),
        encoding="utf-8",
    )
    (index_dir / "tool_index.jsonl").write_text('{"name":"stale_tool"}\n', encoding="utf-8")
    container = containers._container_row(spec, image)
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setattr(containers, "PREBUILT_INDEX_ROOT", prebuilt_root)
    monkeypatch.setattr(containers, "discover_container_commands", lambda _container: ["live_tool"])

    refreshed = containers.refresh_container_index(container, harvest_help=False)

    assert refreshed["commands"] == ["live_tool"]
    sidecar_rows = [json.loads(line) for line in (image.parent / "tool_index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["name"] for row in sidecar_rows] == ["live_tool"]


def test_discover_commands_filters_mocked_apptainer_output(tmp_path: Path, monkeypatch):
    image = tmp_path / "runtime.sif"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "commands": ["seed_tool"],
        "image_path": str(image),
    }

    def fake_run_container_text(_image, _args, *, timeout_s):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "mri_info\n"
                "/usr/local/bin/dcm2niix\n"
                "/opt/freesurfer-8.1.0/bin/mri_synthstrip\n"
                "/opt/freesurfer-8.1.0/bin/mri_synthseg\n"
                "/opt/freesurfer-8.1.0/bin/asegstats2table\n"
                "/opt/freesurfer-8.1.0/bin/mrisp_paint\n"
                "/opt/MCR2019b/v97/cefclient/sys/os/glnxa64/libcef.so\n"
                "/opt/freesurfer-8.1.0/lib/libQt5Core.so.5\n"
                "/opt/freesurfer-8.1.0/bin/README.txt\n"
                "/opt/freesurfer-8.1.0/bin/bashcomplete_wb_command\n"
                "/opt/MCR2019b/bin/mex\n"
                "bad/name\n"
                "white space\n"
                "evil;touch-pwned\n"
                "mri_info\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)

    commands = containers.discover_container_commands(container)

    assert commands == [
        "seed_tool",
        "mri_info",
        "dcm2niix",
        "mri_synthstrip",
        "mri_synthseg",
        "asegstats2table",
        "mrisp_paint",
    ]
    assert (tmp_path / "commands.txt").read_text(encoding="utf-8").splitlines() == commands


def test_discover_commands_reads_max_command_env_once(tmp_path: Path, monkeypatch):
    """Verify discovered-output parsing reuses the caller's command limit."""
    image = tmp_path / "runtime.sif"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "commands": [],
        "image_path": str(image),
    }
    env_reads: list[str] = []

    def fake_env_int(name, default):
        env_reads.append(name)
        return 30 if name == "NEUROCADE_COMMAND_DISCOVERY_TIMEOUT" else 2

    def fake_run_container_text(_image, _args, *, timeout_s):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="one\ntwo\nthree\n",
            stderr="",
        )

    monkeypatch.setattr(containers, "_env_int", fake_env_int)
    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)

    commands = containers.discover_container_commands(container)

    assert commands == ["one", "two"]
    assert env_reads.count("NEUROCADE_MAX_DISCOVERED_COMMANDS") == 1


def test_run_container_text_uses_scratch_cwd(tmp_path: Path, monkeypatch):
    image = tmp_path / "runtime.sif"
    image.write_text("fake image", encoding="utf-8")
    scratch_dir = tmp_path / "neurocade_tmp"
    calls: list[dict[str, object]] = []

    def fake_execute(request):
        calls.append(
            {
                "command": request.command,
                "cwd": request.cwd,
                "timeout": request.timeout_s,
                "mode": request.execution_mode,
            }
        )
        return RuntimeExecutionResult(request=request, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(containers, "CONTAINER_PROBE_CWD", scratch_dir)
    monkeypatch.setattr(containers, "execute_runtime_request", fake_execute)
    monkeypatch.delenv("APPTAINER_BIN", raising=False)

    result = containers._run_container_text(image, ["tool", "--help"], timeout_s=7)

    assert result.stdout == "ok"
    assert scratch_dir.is_dir()
    assert calls[0]["cwd"] == scratch_dir
    assert calls[0]["command"] == [
        "apptainer",
        "exec",
        "--net",
        "--network",
        "none",
        "--cleanenv",
        "--no-home",
        str(image),
        "tool",
        "--help",
    ]
    assert calls[0]["timeout"] == 7
    assert calls[0]["mode"] == "runtime-tools-container-probe"


def test_parse_help_text_extracts_synopsis_description_arguments_and_outputs():
    parsed = containers.parse_help_text(
        "dcm2niix",
        """
Usage: dcm2niix [options] <in_folder>

Convert DICOM images to NIfTI.

Options:
  -f, --filename STR   Output filename template
  -z, --compress y/n   Compress output image
  -o, --outdir DIR     Output directory

Outputs:
  NIFTI_FILE           Converted NIfTI image
""",
    )

    assert parsed["synopsis"] == "Usage: dcm2niix [options] <in_folder>"
    assert parsed["description"] == "Convert DICOM images to NIfTI."
    assert {"name": "-z, --compress y/n", "description": "Compress output image"} in parsed["arguments"]
    assert {"name": "-o, --outdir DIR", "description": "Output directory"} in parsed["outputs"]
    assert {"name": "NIFTI_FILE", "description": "Converted NIfTI image"} in parsed["outputs"]


def test_discovery_preserves_filtered_order_within_limit(monkeypatch):
    monkeypatch.setenv("NEUROCADE_MAX_DISCOVERED_COMMANDS", "6")
    output = "\n".join(
        [
            "/opt/freesurfer-8.1.0/bin/README.txt",
            "/opt/freesurfer-8.1.0/lib/libQt5Core.so.5",
            "/opt/freesurfer-8.1.0/bin/zz_support_0",
            "/opt/freesurfer-8.1.0/bin/mri_synthstrip",
            "/opt/freesurfer-8.1.0/bin/mri_synthseg",
            "/opt/freesurfer-8.1.0/bin/asegstats2table",
            "/opt/freesurfer-8.1.0/bin/mrisp_paint",
            "/opt/freesurfer-8.1.0/bin/recon-all",
            "/opt/freesurfer-8.1.0/bin/freeview",
        ]
    )

    commands = containers._discover_commands_from_output(output)

    assert commands == [
        "zz_support_0",
        "mri_synthstrip",
        "mri_synthseg",
        "asegstats2table",
        "mrisp_paint",
        "recon-all",
    ]


def test_refresh_index_harvests_help_and_writes_enriched_rows(tmp_path: Path, monkeypatch):
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "neurocontainer" / "dcm2niix_v1.0.20240202_20260512" / "dcm2niix_v1.0.20240202_20260512.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    def fake_run_container_text(_image, args, *, timeout_s):
        if args[:2] == ["sh", "-lc"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="dcm2niix\n", stderr="")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "Usage: dcm2niix [options] <in_folder>\n\n"
                "Convert DICOM series to NIfTI volumes.\n\n"
                "Options:\n"
                "  -b y/n          BIDS sidecar option\n"
                "  -o DIR          Output directory\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)

    refresh_index(root=root, rebuild_index=True)

    tools_path = root / "llm-data" / "tool-catalog" / "installed_tools.jsonl"
    help_cache = root / "llm-data" / "tool-catalog" / "help_cache.jsonl"
    ignored_path = root / "llm-data" / "tool-catalog" / "ignored_commands.jsonl"
    rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(item for item in rows if item["name"] == "dcm2niix")

    assert row["synopsis"] == "Usage: dcm2niix [options] <in_folder>"
    assert row["description"] == "Convert DICOM series to NIfTI volumes."
    assert "BIDS sidecar option" in row["searchable_text"]
    assert row["raw_help_text"].startswith("Usage: dcm2niix")
    assert help_cache.exists()
    assert ignored_path.exists()
    assert ignored_path.read_text(encoding="utf-8") == ""


def test_harvest_command_help_retries_until_output_looks_like_help(tmp_path: Path, monkeypatch):
    image = tmp_path / "runtime.simg"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "name": "runtime",
        "kind": "neurocontainer",
        "app": "runtime",
        "runtime_version": "1.0",
        "build_date": "latest",
        "image_name": image.name,
        "image_path": str(image),
        "image_size_bytes": image.stat().st_size,
        "image_mtime_ns": image.stat().st_mtime_ns,
        "commands": ["retry_tool"],
    }
    calls: list[tuple[object, object, bool]] = []

    def fake_run_container_text(_image, args, *, timeout_s):
        calls.append(args)
        if args[1] == "--help":
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="ERROR: Flag --help unrecognized")
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="usage: retry_tool [options]\n\nRetry help fallback.\n\noptions:\n  -i FILE   Input image\n",
            stderr="",
        )

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    ignored_rows: list[dict] = []

    rows = containers.build_installed_tool_rows([container], root=tmp_path, harvest_help=True, ignored_rows=ignored_rows)
    cache_rows = [
        json.loads(line)
        for line in (tmp_path / "tool-catalog" / "help_cache.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert [call[1] for call in calls] == ["--help", "-h"]
    assert rows[0]["name"] == "retry_tool"
    assert rows[0]["description"] == "Retry help fallback."
    assert ignored_rows == []
    assert cache_rows[0]["help_arg"] == "-h"


def test_cached_unrecognized_help_output_is_ignored(tmp_path: Path, monkeypatch):
    image = tmp_path / "runtime.simg"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "name": "runtime",
        "kind": "neurocontainer",
        "app": "runtime",
        "runtime_version": "1.0",
        "build_date": "latest",
        "image_name": image.name,
        "image_path": str(image),
        "image_size_bytes": image.stat().st_size,
        "image_mtime_ns": image.stat().st_mtime_ns,
        "commands": ["bad_help"],
    }
    key = containers._container_stat_key(container, "bad_help")
    cache = {
        key: {
            "image_path": key[0],
            "image_name": key[1],
            "command": "bad_help",
            "image_mtime_ns": key[3],
            "image_size_bytes": key[4],
            "help_arg": "--help",
            "returncode": 0,
            "raw_help_text": "ERROR: Flag --help unrecognized.\n--help",
        }
    }
    cache_path = tmp_path / "tool-catalog" / "help_cache.jsonl"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("".join(json.dumps(row) + "\n" for row in cache.values()), encoding="utf-8")
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    ignored_rows: list[dict] = []

    rows = containers.build_installed_tool_rows([container], root=tmp_path, harvest_help=True, ignored_rows=ignored_rows)

    assert rows == []
    assert ignored_rows[0]["name"] == "bad_help"
    assert ignored_rows[0]["ignore_reason"] == "unrecognized_help_arg"


def test_jsonl_dict_loaders_skip_bad_and_non_object_rows(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"image_path":"image.sif","image_name":"image.sif","command":"tool","image_mtime_ns":3,"image_size_bytes":4}',
                "not json",
                '["not", "an", "object"]',
                '{"name":"tool"}',
            ]
        ),
        encoding="utf-8",
    )

    rows = containers._load_jsonl(path)
    cache = containers._load_help_cache(path)

    assert rows == [
        {"image_path": "image.sif", "image_name": "image.sif", "command": "tool", "image_mtime_ns": 3, "image_size_bytes": 4},
        {"name": "tool"},
    ]
    assert list(cache) == [("image.sif", "image.sif", "tool", 3, 4), ("", "", "", 0, 0)]


@pytest.mark.parametrize(
    ("text", "failure_reason", "looks_like_help", "synopsis"),
    [
        ("ERROR: Flag --help unrecognized.\n--help", "unrecognized_help_arg", False, None),
        ("Lacking argument to option --help", "unrecognized_help_arg", False, None),
        ("mgh dirname: invalid option -- 'h'\nTry 'dirname --help' for more information.", "unrecognized_help_arg", False, None),
        ("basename: invalid option -- 'h'\nTry 'basename --help' for more information.", "unrecognized_help_arg", False, None),
        (
            "ERROR: Specify one of --subjects, --subjectsfile --qdec or --qdec-long\n       or run with --help for help.",
            "unrecognized_help_arg",
            False,
            None,
        ),
        ("ERROR: flag --help not recognized", "unrecognized_help_arg", False, None),
        ("ERROR: --help not regocnized", "unrecognized_help_arg", False, None),
        ("error: unexpected argument '--help'", "unrecognized_help_arg", False, None),
        ("ERROR: Option -help unknown\n       Did you really mean --help ?", "unrecognized_help_arg", False, None),
        ("Option: --HELP unknown !!", "unrecognized_help_arg", False, None),
        (
            "Cut-and-paste the following info into your FreeSurfer problem report:\n"
            "---------------------------------------------------------------------\n"
            "Please include the error message generated.",
            "not_help",
            False,
            None,
        ),
        ("tool input op --o outvol\n\n--odt type : output data type\n--help\n", None, True, None),
        (
            "SEGMENTATION OF ASCENDING AROUSAL NETWORK NUCLEI\n\n"
            "This script segments AAN nuclei from the main T1 scan used in the recon-all stream.\n\n"
            "segmentAAN.sh SUBJECT_ID [SUBJECT_DIR]\n\n"
            "SUBJECT_ID: FreeSurfer subject name, e.g., bert\n",
            None,
            True,
            "segmentAAN.sh SUBJECT_ID [SUBJECT_DIR]",
        ),
        (
            "Usage:\n"
            "Converts a cortical stats file created by recon-all into a table.\n"
            "If this file is not found, it will exit with an error.\n"
            "Options:\n"
            "  --subjects subject1 subject2\n",
            None,
            True,
            None,
        ),
        ("error: mri_read(): couldn't determine type of file /tmp/--help", "not_found", False, None),
    ],
)
def test_help_output_classification(text, failure_reason, looks_like_help, synopsis):
    assert containers._help_failure_reason(text) == failure_reason
    assert containers._looks_like_help(text) is looks_like_help
    if synopsis is not None:
        assert containers.parse_help_text("SegmentAAN.sh", text)["synopsis"] == synopsis


def test_harvest_command_help_ignores_long_non_help_text(tmp_path: Path, monkeypatch):
    image = tmp_path / "runtime.simg"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "name": "runtime",
        "kind": "neurocontainer",
        "app": "runtime",
        "runtime_version": "1.0",
        "build_date": "latest",
        "image_name": image.name,
        "image_path": str(image),
        "image_size_bytes": image.stat().st_size,
        "image_mtime_ns": image.stat().st_mtime_ns,
        "commands": ["unminimize"],
    }

    def fake_run_container_text(_image, args, *, timeout_s):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "This system has been minimized by removing packages and content that are not required "
                "on a system that users do not log into. This script restores content and packages."
            ),
            stderr="",
        )

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    ignored_rows: list[dict] = []

    rows = containers.build_installed_tool_rows([container], root=tmp_path, harvest_help=True, ignored_rows=ignored_rows)

    assert rows == []
    assert ignored_rows[0]["name"] == "unminimize"
    assert ignored_rows[0]["ignore_reason"] == "not_help"


def test_refresh_index_writes_ignored_commands_when_help_never_succeeds(tmp_path: Path, monkeypatch):
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "neurocontainer" / "runtime_1.0_latest" / "runtime_1.0_latest.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    def fake_run_container_text(_image, args, *, timeout_s):
        if args[:2] == ["sh", "-lc"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ignored_tool\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr=f"ERROR: Flag {args[1]} unrecognized")

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)

    refresh_index(root=root)

    tools_path = root / "llm-data" / "tool-catalog" / "installed_tools.jsonl"
    ignored_path = root / "llm-data" / "tool-catalog" / "ignored_commands.jsonl"
    rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ignored_rows = [json.loads(line) for line in ignored_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert all(row["name"] != "ignored_tool" for row in rows)
    assert ignored_rows[0]["name"] == "ignored_tool"
    assert [attempt["help_arg"] for attempt in ignored_rows[0]["attempts"]] == ["--help", "-h", "--h", "help"]
    assert {attempt["reason"] for attempt in ignored_rows[0]["attempts"]} == {"unrecognized_help_arg"}


def test_index_cap_applies_after_help_filtering(tmp_path: Path, monkeypatch):
    image = tmp_path / "runtime.simg"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "name": "runtime",
        "kind": "neurocontainer",
        "app": "runtime",
        "runtime_version": "1.0",
        "build_date": "latest",
        "image_name": image.name,
        "image_path": str(image),
        "image_size_bytes": image.stat().st_size,
        "image_mtime_ns": image.stat().st_mtime_ns,
        "commands": ["bad_1", "good_1", "bad_2", "good_2", "good_3"],
    }

    def fake_run_container_text(_image, args, *, timeout_s):
        command = args[0]
        if command.startswith("bad"):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="ERROR: Flag --help unrecognized")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=f"Usage: {command} [options]\n\nUseful command.\n\nOptions:\n  --input FILE   Input file\n",
            stderr="",
        )

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setenv("NEUROCADE_MAX_INDEXED_COMMANDS", "2")
    ignored_rows: list[dict] = []

    rows = containers.build_installed_tool_rows([container], root=tmp_path, harvest_help=True, ignored_rows=ignored_rows)

    assert [row["name"] for row in rows] == ["good_1", "good_2"]
    assert [row["name"] for row in ignored_rows] == ["bad_1", "bad_2"]


def test_build_installed_tool_rows_skips_removed_freesurfer_commands(tmp_path: Path, monkeypatch):
    image = tmp_path / "freesurfer.simg"
    image.write_text("fake image", encoding="utf-8")
    container = {
        "name": "freesurfer",
        "kind": "neurocontainer",
        "app": "freesurfer",
        "runtime_version": "8.1.0",
        "build_date": "latest",
        "image_name": image.name,
        "image_path": str(image),
        "image_size_bytes": image.stat().st_size,
        "image_mtime_ns": image.stat().st_mtime_ns,
        "commands": ["old_tool", "mri_info"],
    }

    def fake_run_container_text(_image, args, *, timeout_s):
        if args[0] == "old_tool":
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="old_tool has been removed from this version of freesurfer\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="usage: mri_info [options]\n\nReport MRI volume information.\n",
            stderr="",
        )

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))

    ignored_rows: list[dict] = []
    rows = containers.build_installed_tool_rows([container], root=tmp_path, harvest_help=True, ignored_rows=ignored_rows)

    assert [row["name"] for row in rows] == ["mri_info"]
    assert ignored_rows[0]["name"] == "old_tool"
    assert ignored_rows[0]["ignore_reason"] == "removed"


def test_refresh_index_no_harvest_help_discovers_commands_without_help_metadata(tmp_path: Path, monkeypatch):
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "neurocontainer" / "ants_2.5.0_20250101" / "ants_2.5.0_20250101.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    calls: list[tuple[Path, str, bool]] = []

    def fake_run_container_text(_image, args, *, timeout_s):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="N4BiasFieldCorrection\n", stderr="")

    monkeypatch.setattr(containers, "_run_container_text", fake_run_container_text)

    refresh_index(root=root, harvest_help=False)

    tools_path = root / "llm-data" / "tool-catalog" / "installed_tools.jsonl"
    rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    names = {row["name"] for row in rows}

    assert "N4BiasFieldCorrection" in names
    assert all(args[:2] == ["sh", "-lc"] for args in calls)
    discovered = next(row for row in rows if row["name"] == "N4BiasFieldCorrection")
    assert discovered["raw_help_text"] is None
    assert discovered["arguments"] == []


def test_merge_container_indexes_reads_sidecars_without_apptainer(tmp_path: Path, monkeypatch):
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "neurocontainer" / "ants_2.5.0_20250101" / "ants_2.5.0_20250101.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.setattr(containers, "_run_container_text", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no apptainer probing")))
    container = containers.scan_containers(root)[0]
    container["commands"] = ["N4BiasFieldCorrection"]
    tool_row = containers._tool_row(
        container,
        "N4BiasFieldCorrection",
        {
            "synopsis": "Usage: N4BiasFieldCorrection [options]",
            "description": "Bias field correction.",
            "arguments": [],
            "outputs": [],
            "raw_help_text": "Usage: N4BiasFieldCorrection [options]",
        },
    )
    (image.parent / "tool_index.jsonl").write_text(json.dumps(tool_row) + "\n", encoding="utf-8")
    (image.parent / "ignored_commands.jsonl").write_text("", encoding="utf-8")
    metadata = containers._container_index_metadata(container, [tool_row], [])
    (image.parent / "index_meta.json").write_text(json.dumps(metadata), encoding="utf-8")

    indexed_containers, tool_rows, ignored_rows = containers.merge_container_indexes(root=root)
    global_rows = [
        json.loads(line)
        for line in (root / "llm-data" / "tool-catalog" / "installed_tools.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert [row["name"] for row in indexed_containers] == ["ants"]
    assert [row["name"] for row in tool_rows] == ["N4BiasFieldCorrection"]
    assert ignored_rows == []
    assert global_rows == [tool_row]


def test_retrieval_finds_help_only_terms():
    records = [
        {
            "name": "dcm2niix",
            "container_command": "dcm2niix",
            "description": "DICOM converter",
            "arguments": [{"name": "-b", "description": "BIDS sidecar option"}],
            "outputs": [],
            "raw_help_text": "",
            "searchable_text": "",
        },
        {
            "name": "mri_info",
            "container_command": "mri_info",
            "description": "Volume header information",
            "searchable_text": "",
        },
    ]

    hits = hybrid_rank("BIDS sidecar", records, n_results=1)

    assert hits[0]["name"] == "dcm2niix"


def test_install_core_indexes_new_containers_and_merges_once(monkeypatch, tmp_path: Path):
    """Verify core install indexes newly created images and merges once."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)

    installed: list[str] = []
    index_calls = []
    merge_calls = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        name_by_image = {spec.image_name: spec.name for spec in CORE_SPECS.values()}
        installed.append(name_by_image[target.name])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fake image", encoding="utf-8")

    def fake_refresh_container_index(container, *, root=None, harvest_help=True, use_prebuilt_index=True):
        index_calls.append(container["name"])

    def fake_merge_container_indexes(*, root=None):
        merge_calls.append(root)
        return [], [], []

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    monkeypatch.setattr(containers, "refresh_container_index", fake_refresh_container_index)
    monkeypatch.setattr(containers, "merge_container_indexes", fake_merge_container_indexes)

    install_core(source="auto")

    assert set(installed) == {"fastsurfer", "bash_image", "dcm2niix"}
    assert index_calls == ["fastsurfer", "bash_image", "dcm2niix"]
    assert merge_calls == [None]


def test_install_core_indexes_existing_containers_when_index_missing(monkeypatch, tmp_path: Path):
    """Verify install core refreshes indexes for prefetched images."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    for spec in [CORE_SPECS["fastsurfer"], CORE_SPECS["bash_image"], CORE_SPECS["dcm2niix"]]:
        image = containers.default_image_path(spec)
        image.parent.mkdir(parents=True)
        image.write_text("fake image", encoding="utf-8")

    install_calls = []
    index_calls = []
    merge_calls = []
    monkeypatch.setattr(containers, "_pull_or_build", lambda *args, **kwargs: install_calls.append((args, kwargs)))
    monkeypatch.setattr(containers, "refresh_container_index", lambda *args, **kwargs: index_calls.append((args, kwargs)))
    monkeypatch.setattr(containers, "merge_container_indexes", lambda *args, **kwargs: merge_calls.append((args, kwargs)))

    install_core(source="auto")

    assert install_calls == []
    assert [args[0][0]["name"] for args in index_calls] == ["fastsurfer", "bash_image", "dcm2niix"]
    assert len(merge_calls) == 1


def test_install_core_skips_hashing_existing_current_indexes(monkeypatch, tmp_path: Path):
    """Verify startup install checks do not hash already-indexed core images."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)

    for name in ("fastsurfer", "bash_image", "dcm2niix"):
        spec = CORE_SPECS[name]
        image = containers.default_image_path(spec)
        image.parent.mkdir(parents=True)
        image.write_text("fake image", encoding="utf-8")
        row = containers._container_row_without_hash(spec, image)
        (image.parent / "index_meta.json").write_text(
            json.dumps(
                {
                    "schema_version": containers.CONTAINER_INDEX_SCHEMA_VERSION,
                    "image_path": row["image_path"],
                    "image_name": row["image_name"],
                    "image_mtime_ns": row["image_mtime_ns"],
                    "image_size_bytes": row["image_size_bytes"],
                }
            ),
            encoding="utf-8",
        )
        (image.parent / "tool_index.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(containers, "_sha256_file", lambda _path: (_ for _ in ()).throw(AssertionError("should not hash existing images")))
    monkeypatch.setattr(containers, "refresh_container_index", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not refresh")))
    monkeypatch.setattr(containers, "merge_container_indexes", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not merge")))

    install_core(source="auto")


def test_check_core_fast_validates_existing_indexes(monkeypatch, tmp_path: Path):
    """Verify the startup core check validates paths and sidecars without image hashing."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    rows = []
    for name in ("fastsurfer", "bash_image", "dcm2niix"):
        spec = CORE_SPECS[name]
        image = containers.default_image_path(spec, tmp_path)
        image.parent.mkdir(parents=True)
        image.write_text("fake image", encoding="utf-8")
        row = containers._container_row_without_hash(spec, image)
        rows.append(row)
        (image.parent / "index_meta.json").write_text("{}", encoding="utf-8")
        (image.parent / "tool_index.jsonl").write_text("{}\n", encoding="utf-8")

    inventory = tmp_path / "tool-catalog" / "installed_containers.json"
    tools = tmp_path / "tool-catalog" / "installed_tools.jsonl"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({"containers": rows}), encoding="utf-8")
    tools.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(containers, "_sha256_file", lambda _path: (_ for _ in ()).throw(AssertionError("should not hash")))
    validated_images: list[Path] = []
    monkeypatch.setattr(containers, "_validate_container_architecture", lambda image: validated_images.append(Path(image)))

    containers.check_core_fast(root=tmp_path)

    assert validated_images == [Path(row["image_path"]) for row in rows]


def test_check_core_fast_fails_when_sidecar_missing(monkeypatch, tmp_path: Path):
    """Verify the startup core check falls back when a per-container index is missing."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    rows = []
    for name in ("fastsurfer", "bash_image", "dcm2niix"):
        spec = CORE_SPECS[name]
        image = containers.default_image_path(spec, tmp_path)
        image.parent.mkdir(parents=True)
        image.write_text("fake image", encoding="utf-8")
        rows.append(containers._container_row_without_hash(spec, image))

    inventory = tmp_path / "tool-catalog" / "installed_containers.json"
    tools = tmp_path / "tool-catalog" / "installed_tools.jsonl"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({"containers": rows}), encoding="utf-8")
    tools.write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="sidecar"):
        containers.check_core_fast(root=tmp_path)


def test_install_core_can_skip_refresh(monkeypatch, tmp_path: Path):
    """Verify install core can skip the expensive index refresh."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)

    installed: list[str] = []
    index_calls = []
    merge_calls = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        name_by_image = {spec.image_name: spec.name for spec in CORE_SPECS.values()}
        installed.append(name_by_image[target.name])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fake image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    monkeypatch.setattr(containers, "refresh_container_index", lambda *args, **kwargs: index_calls.append((args, kwargs)))
    monkeypatch.setattr(containers, "merge_container_indexes", lambda **kwargs: merge_calls.append(kwargs))

    install_core(source="auto", refresh=False)

    assert set(installed) == {"fastsurfer", "bash_image", "dcm2niix"}
    assert index_calls == []
    assert merge_calls == []


def test_install_core_parallelizes_direct_downloads(monkeypatch, tmp_path: Path):
    """Verify core install overlaps independent direct image downloads."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setenv("NEUROCADE_CONTAINER_DOWNLOAD_JOBS", "2")
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)

    lock = threading.Lock()
    all_started = threading.Event()
    release = threading.Event()
    active = 0
    started = 0
    max_active = 0

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        nonlocal active, started, max_active
        with lock:
            active += 1
            started += 1
            max_active = max(max_active, active)
            if started == 2:
                all_started.set()
                release.set()
        if not release.wait(timeout=2):
            raise AssertionError("parallel download workers did not overlap")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fake image", encoding="utf-8")
        with lock:
            active -= 1

    monkeypatch.setattr(containers, "_download_file", fake_download)

    install_core(source="auto", refresh=False)

    assert all_started.is_set()
    assert max_active == 2


def test_install_core_auto_fallback_removes_partial_direct_download(monkeypatch, tmp_path: Path):
    """Verify auto fallback does not mistake a partial direct download for an installed image."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)

    fastsurfer_target = containers.default_image_path(CORE_SPECS["fastsurfer"])
    pulls: list[tuple[Path, str, bool]] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target == fastsurfer_target:
            target.write_text("partial image", encoding="utf-8")
            raise OSError("download interrupted")
        target.write_text("fake image", encoding="utf-8")

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        assert not target.exists()
        pulls.append((target, uri, dry_run))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fallback image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    install_core(source="auto", refresh=False)

    assert pulls == [(fastsurfer_target, "docker://vnmd/fastsurfer_2.4.2:20260115", False)]
    assert fastsurfer_target.read_text(encoding="utf-8") == "fallback image"


def test_install_core_auto_falls_back_after_direct_arch_mismatch(monkeypatch, tmp_path: Path):
    """Verify core install validates the parallel direct-download path."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    fastsurfer_target = containers.default_image_path(CORE_SPECS["fastsurfer"])
    pulls: list[tuple[Path, str, bool]] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("direct amd64 image" if target == fastsurfer_target else "direct ok image", encoding="utf-8")

    def fake_validate(target):
        if target.read_text(encoding="utf-8") == "direct amd64 image":
            raise RuntimeError("image is amd64, but the Apptainer guest is aarch64")

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        assert target == fastsurfer_target
        assert not target.exists()
        pulls.append((target, uri, dry_run))
        target.write_text("fallback arm64 image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(containers, "_validate_container_architecture", fake_validate)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    install_core(source="auto", refresh=False)

    assert pulls == [(fastsurfer_target, "docker://vnmd/fastsurfer_2.4.2:20260115", False)]
    assert fastsurfer_target.read_text(encoding="utf-8") == "fallback arm64 image"


def test_install_core_existing_incompatible_image_reinstalls(monkeypatch, tmp_path: Path):
    """Verify core install validates existing images before reusing them."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    fastsurfer_target = containers.default_image_path(CORE_SPECS["fastsurfer"])
    fastsurfer_target.parent.mkdir(parents=True)
    fastsurfer_target.write_text("old amd64 image", encoding="utf-8")
    pulls: list[tuple[Path, str, bool]] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("direct amd64 image" if target == fastsurfer_target else "direct ok image", encoding="utf-8")

    def fake_validate(target):
        if target.read_text(encoding="utf-8") in {"old amd64 image", "direct amd64 image"}:
            raise RuntimeError("image is amd64, but the Apptainer guest is aarch64")

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        assert target == fastsurfer_target
        assert not target.exists()
        pulls.append((target, uri, dry_run))
        target.write_text("fallback arm64 image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(containers, "_validate_container_architecture", fake_validate)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    install_core(source="auto", refresh=False)

    assert pulls == [(fastsurfer_target, "docker://vnmd/fastsurfer_2.4.2:20260115", False)]
    assert fastsurfer_target.read_text(encoding="utf-8") == "fallback arm64 image"


def test_prefetch_core_does_not_validate_existing_direct_images(monkeypatch, tmp_path: Path):
    """Verify prefetch stays a download-only step when Apptainer is not ready yet."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    dcm2niix_target = containers.default_image_path(CORE_SPECS["dcm2niix"])
    dcm2niix_target.parent.mkdir(parents=True)
    dcm2niix_target.write_text("old amd64 image", encoding="utf-8")
    downloaded: list[Path] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        downloaded.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("direct ok image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(
        containers,
        "_validate_container_architecture",
        lambda _target: pytest.fail("prefetch should not invoke Apptainer architecture validation"),
    )

    containers.prefetch_core()

    assert dcm2niix_target not in downloaded
    assert dcm2niix_target.read_text(encoding="utf-8") == "old amd64 image"


def test_prefetch_core_downloads_without_arch_validation(monkeypatch, tmp_path: Path):
    """Verify fresh prefetch does not require Apptainer to be installed yet."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    downloaded: list[Path] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        downloaded.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("direct image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(
        containers,
        "_validate_container_architecture",
        lambda _target: pytest.fail("prefetch should not invoke Apptainer architecture validation"),
    )

    containers.prefetch_core()

    assert downloaded


def test_prefetch_core_skips_freesurfer_by_default(monkeypatch, tmp_path: Path):
    """Verify prefetch core skips full FreeSurfer unless explicitly requested."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    downloaded: list[str] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        name_by_image = {spec.image_name: spec.name for spec in CORE_SPECS.values()}
        downloaded.append(name_by_image[target.name])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fake image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)

    containers.prefetch_core()

    assert set(downloaded) == {"fastsurfer", "bash_image", "dcm2niix"}
    assert core_install_plan(root=tmp_path) == ("fastsurfer", "bash_image", "dcm2niix")
    refresh_index(root=tmp_path, harvest_help=False)
    inventory_path = tmp_path / "tool-catalog" / "installed_containers.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert {row["name"] for row in inventory["containers"]} == {"fastsurfer", "bash_image", "dcm2niix"}
    assert not containers.default_image_path(CORE_SPECS["freesurfer"]).with_name("tool_index.jsonl").exists()


def test_prefetch_core_includes_freesurfer_with_opt_in(monkeypatch, tmp_path: Path):
    """Verify prefetch core can explicitly include the full FreeSurfer image."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: tmp_path / "license.txt")
    downloaded: list[str] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        name_by_image = {spec.image_name: spec.name for spec in CORE_SPECS.values()}
        downloaded.append(name_by_image[target.name])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fake image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)

    containers.prefetch_core(include_freesurfer=True)

    assert set(downloaded) == {"fastsurfer", "bash_image", "dcm2niix", "freesurfer"}


def test_prefetch_core_with_freesurfer_requires_license(monkeypatch, tmp_path: Path):
    """Verify explicit FreeSurfer prefetch keeps the license requirement."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)

    with pytest.raises(RuntimeError, match="freesurfer requires a FreeSurfer license"):
        containers.prefetch_core(include_freesurfer=True)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            ["--no-refresh"],
            {
                "source": "auto",
                "dry_run": False,
                "refresh": False,
                "harvest_help": True,
                "rebuild_index": False,
                "include_freesurfer": None,
            },
        ),
        (
            ["--no-harvest-help"],
            {
                "source": "auto",
                "dry_run": False,
                "refresh": True,
                "harvest_help": False,
                "rebuild_index": False,
                "include_freesurfer": None,
            },
        ),
        (
            ["--rebuild-index"],
            {
                "source": "auto",
                "dry_run": False,
                "refresh": True,
                "harvest_help": True,
                "rebuild_index": True,
                "include_freesurfer": None,
            },
        ),
        (
            ["--with-freesurfer"],
            {
                "source": "auto",
                "dry_run": False,
                "refresh": True,
                "harvest_help": True,
                "rebuild_index": False,
                "include_freesurfer": True,
            },
        ),
    ],
)
def test_install_core_cli_passes_flags(monkeypatch, args, expected):
    """Verify install core CLI flags are passed through."""
    calls = []

    def fake_install_core(*, source, dry_run, refresh, harvest_help, rebuild_index, include_freesurfer):
        calls.append(
            {
                "source": source,
                "dry_run": dry_run,
                "refresh": refresh,
                "harvest_help": harvest_help,
                "rebuild_index": rebuild_index,
                "include_freesurfer": include_freesurfer,
            }
        )

    monkeypatch.setattr(containers, "install_core", fake_install_core)

    containers.main(["install", "core", *args])

    assert calls == [expected]


def test_prefetch_core_cli_passes_with_freesurfer(monkeypatch):
    """Verify prefetch core CLI flags are passed through."""
    calls = []

    def fake_prefetch_core(*, dry_run, include_freesurfer):
        calls.append({"dry_run": dry_run, "include_freesurfer": include_freesurfer})

    monkeypatch.setattr(containers, "prefetch_core", fake_prefetch_core)

    containers.main(["prefetch", "core", "--with-freesurfer"])

    assert calls == [{"dry_run": False, "include_freesurfer": True}]


def test_check_core_fast_cli_passes_with_freesurfer(monkeypatch, capsys):
    """Verify check core --fast CLI flags are passed through."""
    calls = []

    def fake_check_core_fast(*, include_freesurfer):
        calls.append(include_freesurfer)

    monkeypatch.setattr(containers, "check_core_fast", fake_check_core_fast)

    containers.main(["check", "core", "--fast", "--with-freesurfer"])

    assert calls == [True]
    assert "lightweight startup check" in capsys.readouterr().out


def test_install_container_cli_honors_no_harvest_help(monkeypatch):
    """Verify CLI --no-harvest-help is passed through for single-container install."""
    calls = []

    def fake_install_container(name, *, source, dry_run, refresh, harvest_help, rebuild_index):
        calls.append(
            {
                "name": name,
                "source": source,
                "dry_run": dry_run,
                "refresh": refresh,
                "harvest_help": harvest_help,
                "rebuild_index": rebuild_index,
            }
        )

    monkeypatch.setattr(containers, "install_container", fake_install_container)

    containers.main(["install", "dcm2niix", "--no-harvest-help"])

    assert calls == [
        {
            "name": "dcm2niix",
            "source": "auto",
            "dry_run": False,
            "refresh": True,
            "harvest_help": False,
            "rebuild_index": False,
        }
    ]


def test_install_container_indexes_new_image_and_merges(monkeypatch, tmp_path: Path):
    """Verify single-container install indexes only the newly created image."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setenv("APPTAINER_BIN", "apptainer")
    index_calls = []
    merge_calls = []

    def fake_pull_or_build(spec, target, *, source, dry_run):
        target.parent.mkdir(parents=True)
        target.write_text("fake image", encoding="utf-8")

    def fake_refresh_container_index(container, *, root=None, harvest_help=True, use_prebuilt_index=True):
        index_calls.append(container["name"])

    def fake_merge_container_indexes(*, root=None):
        merge_calls.append(root)
        return [], [], []

    monkeypatch.setattr(containers, "_pull_or_build", fake_pull_or_build)
    monkeypatch.setattr(containers, "refresh_container_index", fake_refresh_container_index)
    monkeypatch.setattr(containers, "merge_container_indexes", fake_merge_container_indexes)

    result = containers.install_container("dcm2niix", source="upstream")

    assert result is not None
    assert result["name"] == "dcm2niix"
    assert index_calls == ["dcm2niix"]
    assert merge_calls == [None]


def test_install_container_passes_harvest_help_to_refresh(monkeypatch, tmp_path: Path):
    """Verify single-container installs can skip help harvesting during refresh."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    calls: list[dict[str, object]] = []

    def fake_pull_or_build(_spec, target, *, source, dry_run):
        target.parent.mkdir(parents=True)
        target.write_text("fake image", encoding="utf-8")

    def fake_refresh_container_index(container, *, root=None, harvest_help=True, use_prebuilt_index=True):
        calls.append(
            {
                "name": container["name"],
                "root": root,
                "harvest_help": harvest_help,
                "use_prebuilt_index": use_prebuilt_index,
            }
        )

    monkeypatch.setattr(containers, "_pull_or_build", fake_pull_or_build)
    monkeypatch.setattr(containers, "refresh_container_index", fake_refresh_container_index)
    monkeypatch.setattr(containers, "merge_container_indexes", lambda *args, **kwargs: ([], [], []))

    containers.install_container("dcm2niix", harvest_help=False)

    assert calls == [{"name": "dcm2niix", "root": None, "harvest_help": False, "use_prebuilt_index": True}]


def test_install_container_existing_image_does_not_index(monkeypatch, tmp_path: Path):
    """Verify already-installed images do not trigger index work."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    image = containers.default_image_path(CORE_SPECS["dcm2niix"])
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    install_calls = []
    index_calls = []
    merge_calls = []
    monkeypatch.setattr(containers, "_pull_or_build", lambda *args, **kwargs: install_calls.append((args, kwargs)))
    monkeypatch.setattr(containers, "refresh_container_index", lambda *args, **kwargs: index_calls.append((args, kwargs)))
    monkeypatch.setattr(containers, "merge_container_indexes", lambda *args, **kwargs: merge_calls.append((args, kwargs)))

    result = containers.install_container("dcm2niix")

    assert result is None
    assert install_calls == []
    assert index_calls == []
    assert merge_calls == []


def test_core_install_plan_includes_freesurfer_only_with_opt_in(monkeypatch, tmp_path: Path):
    """Verify FreeSurfer is excluded by default and included only on opt-in."""
    monkeypatch.delenv("NEUROCADE_INSTALL_FREESURFER", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)
    assert core_install_plan(root=tmp_path) == ("fastsurfer", "bash_image", "dcm2niix")

    monkeypatch.setattr(containers, "license_path", lambda root=None: tmp_path / "license.txt")
    assert core_install_plan(root=tmp_path) == ("fastsurfer", "bash_image", "dcm2niix")
    assert core_install_plan(root=tmp_path, include_freesurfer=True) == ("fastsurfer", "bash_image", "dcm2niix", "freesurfer")

    monkeypatch.setenv("NEUROCADE_INSTALL_FREESURFER", "1")
    assert core_install_plan(root=tmp_path) == ("fastsurfer", "bash_image", "dcm2niix", "freesurfer")


def test_install_core_with_freesurfer_requires_license(monkeypatch, tmp_path: Path):
    """Verify explicit FreeSurfer core install keeps the license requirement."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: None)

    with pytest.raises(RuntimeError, match="freesurfer requires a FreeSurfer license"):
        install_core(source="auto", dry_run=True, refresh=False, include_freesurfer=True)


def test_status_keeps_installed_freesurfer_when_licensed(monkeypatch, tmp_path: Path):
    """Verify an existing installed FreeSurfer image remains visible when licensed."""
    container_root = tmp_path / "containers"
    tool_catalog = tmp_path / "tool-catalog"
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tool_catalog))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)
    monkeypatch.setattr(containers, "license_path", lambda root=None: tmp_path / "license.txt")
    image = containers.default_image_path(CORE_SPECS["freesurfer"], tmp_path)
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    status = container_status(root=tmp_path)

    freesurfer = next(row for row in status["containers"] if row["name"] == "freesurfer")
    assert freesurfer["installed"] is True


def test_container_status_reports_index_and_source_order(tmp_path: Path, monkeypatch):
    """Verify status reports index state and install source priority."""
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "neurocontainer" / "dcm2niix_v1.0.20240202_20260512" / "dcm2niix_v1.0.20240202_20260512.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    refresh_index(root=root, harvest_help=False)
    status = container_status(root=root)
    dcm2niix = next(row for row in status["containers"] if row["name"] == "dcm2niix")
    fastsurfer = next(row for row in status["containers"] if row["name"] == "fastsurfer")

    assert status["index_generated_at"]
    assert dcm2niix["installed"] is True
    assert dcm2niix["indexed"] is True
    assert dcm2niix["missing_message"] is None
    assert dcm2niix["source_order"] == ["fileshare", "docker"]
    assert "install fastsurfer" in fastsurfer["missing_message"]
    assert fastsurfer["source_order"] == ["fileshare", "docker"]


def test_auto_install_falls_back_after_fileshare_failure(monkeypatch, tmp_path: Path):
    """Verify auto install falls back to Docker after fileshare failure."""
    target = tmp_path / "fastsurfer.sif"
    calls: list[tuple[object, object, bool]] = []
    monkeypatch.setenv("APPTAINER_BIN", "apptainer")

    def failing_download(_url, _target, *, expected_sha256=None, dry_run=False):
        raise OSError("fileshare unavailable")

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        calls.append((target, uri, dry_run))

    monkeypatch.setattr(containers, "_download_file", failing_download)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    containers._pull_or_build(CORE_SPECS["fastsurfer"], target, source="auto")

    assert calls == [(target, "docker://vnmd/fastsurfer_2.4.2:20260115", False)]


def test_auto_install_falls_back_after_fileshare_arch_mismatch(monkeypatch, tmp_path: Path):
    """Verify auto install replaces an incompatible direct image with an OCI pull."""
    target = tmp_path / "fastsurfer.sif"
    pulls: list[tuple[object, object, bool]] = []

    def fake_download(_url, target, *, expected_sha256=None, dry_run=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("direct amd64 image", encoding="utf-8")

    def fake_validate(image):
        if image.read_text(encoding="utf-8") == "direct amd64 image":
            raise RuntimeError("image is amd64, but the Apptainer guest is aarch64")

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        assert not target.exists()
        pulls.append((target, uri, dry_run))
        target.write_text("fallback arm64 image", encoding="utf-8")

    monkeypatch.setattr(containers, "_download_file", fake_download)
    monkeypatch.setattr(containers, "_validate_container_architecture", fake_validate)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    containers._pull_or_build(CORE_SPECS["fastsurfer"], target, source="auto")

    assert pulls == [(target, "docker://vnmd/fastsurfer_2.4.2:20260115", False)]
    assert target.read_text(encoding="utf-8") == "fallback arm64 image"


def test_existing_incompatible_image_reinstalls_from_upstream(monkeypatch, tmp_path: Path):
    """Verify an existing incompatible image is not reused in auto mode."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    image = containers.default_image_path(CORE_SPECS["dcm2niix"])
    image.parent.mkdir(parents=True)
    image.write_text("old amd64 image", encoding="utf-8")
    pulls: list[tuple[Path, str, bool]] = []

    def fake_validate(target):
        if target.read_text(encoding="utf-8") == "old amd64 image":
            raise RuntimeError("image is amd64, but the Apptainer guest is aarch64")

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        assert not target.exists()
        pulls.append((target, uri, dry_run))
        target.write_text("new arm64 image", encoding="utf-8")

    monkeypatch.setattr(containers, "_validate_container_architecture", fake_validate)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    result = containers.install_container("dcm2niix", refresh=False)

    assert result is not None
    assert result["name"] == "dcm2niix"
    assert pulls == [(image, "docker://vnmd/dcm2niix_v1.0.20240202:20260512", False)]
    assert image.read_text(encoding="utf-8") == "new arm64 image"


def test_bash_image_can_build_inside_lima_without_escape_hatch(monkeypatch, tmp_path: Path):
    """Verify bash_image can fall back to its Docker Buildfile on macOS/Lima."""
    wrapper = tmp_path / ".apptainer" / "bin" / "apptainer"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setenv("APPTAINER_BIN", str(wrapper))
    monkeypatch.delenv("NEUROCADE_ALLOW_LOCAL_CONTAINER_BUILDS", raising=False)
    target = tmp_path / "bash-image-python-3.12.sif"
    builds: list[tuple[Path, Path, bool]] = []

    def fake_lima_build(target, build_file, *, dry_run=False):
        builds.append((target, build_file, dry_run))
        target.write_text("built image", encoding="utf-8")

    monkeypatch.setattr(containers, "_run_lima_apptainer_build", fake_lima_build)
    monkeypatch.setattr(containers, "_validate_container_architecture", lambda _target: None)

    containers._fallback_install(CORE_SPECS["bash_image"], target)

    assert builds == [
        (
            target,
            containers.find_repo_root() / "packages/neurocade-runtime-tools/src/neurocade_runtime_tools/bash_python_image/Buildfile",
            False,
        )
    ]


def test_remote_container_install_allows_missing_integrity_metadata(monkeypatch, tmp_path: Path):
    """Allow remote image sources without immutable metadata while verification is deferred."""
    target = tmp_path / "container.sif"
    calls: list[tuple[list[str], Path, bool]] = []

    def fake_run(command, *, cwd, dry_run=False):
        calls.append((command, cwd, dry_run))

    monkeypatch.setattr(containers, "_run", fake_run)
    monkeypatch.setenv("APPTAINER_BIN", "apptainer")

    containers._download_file("https://example.invalid/container.sif", target, expected_sha256=None, dry_run=True)
    containers._run_apptainer_pull(target, "docker://vnmd/fastsurfer_2.4.2:20260115", dry_run=True)

    assert calls == [
        (
            [
                "curl",
                "-fL",
                "--retry",
                containers.CURL_RETRY_COUNT,
                "--retry-delay",
                containers.CURL_RETRY_DELAY_SECONDS,
                "--continue-at",
                "-",
                "https://example.invalid/container.sif",
                "-o",
                str(target.with_name("container.sif.partial")),
            ],
            tmp_path,
            True,
        )
    ]


def test_download_file_preserves_partial_after_transport_failure(monkeypatch, tmp_path: Path):
    """Verify interrupted downloads keep a resumable partial away from the final image path."""
    target = tmp_path / "container.sif"

    def fake_run(_command, *, cwd, dry_run=False):
        partial = cwd / "container.sif.partial"
        partial.write_text("partial", encoding="utf-8")
        raise OSError("download interrupted")

    monkeypatch.setattr(containers, "_run", fake_run)

    try:
        containers._download_file("https://example.invalid/container.sif", target)
    except OSError:
        pass
    else:
        raise AssertionError("download failure did not propagate")

    assert not target.exists()
    assert target.with_name("container.sif.partial").read_text(encoding="utf-8") == "partial"


def test_download_file_removes_partial_after_hash_mismatch(monkeypatch, tmp_path: Path):
    """Verify failed integrity checks discard partial downloads."""
    target = tmp_path / "container.sif"

    def fake_run(_command, *, cwd, dry_run=False):
        partial = cwd / "container.sif.partial"
        partial.write_text("corrupt", encoding="utf-8")

    monkeypatch.setattr(containers, "_run", fake_run)
    monkeypatch.setattr(containers, "_sha256_file", lambda _path: "bad")

    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        containers._download_file("https://example.invalid/container.sif", target, expected_sha256="good")

    assert not target.exists()
    assert not target.with_name("container.sif.partial").exists()


def test_install_unknown_name_resolves_neurocontainer(monkeypatch, tmp_path: Path):
    """Verify install unknown name resolves NeuroContainer repository."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setenv("APPTAINER_BIN", "apptainer")

    def fake_http_json(url: str):
        if "/repositories/" in url and "/tags" in url:
            return {"results": [{"name": "20240101"}, {"name": "20250101"}]}
        return {
            "results": [
                {"name": "matlab_runtime_R2023b", "namespace": "vnmd"},
                {"name": "matlab_R2023b", "namespace": "vnmd"},
            ]
        }

    calls: list[tuple[Path, str, bool]] = []

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        calls.append((target, uri, dry_run))

    monkeypatch.setattr(containers, "_http_json", fake_http_json)
    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    containers.install_container("matlab", source="upstream", dry_run=True)

    assert calls == [
        (
            tmp_path / "containers" / "neurocontainer" / "matlab_R2023b_20250101" / "matlab_R2023b_20250101.simg",
            "docker://vnmd/matlab_R2023b:20250101",
            True,
        )
    ]


def test_install_freesurfer_uses_latest_neurocontainer_image(monkeypatch, tmp_path: Path):
    """Verify managed FreeSurfer installs from the latest versioned NeuroContainer."""
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(tmp_path / "containers"))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(tmp_path / "tool-catalog"))
    monkeypatch.setenv("APPTAINER_BIN", "apptainer")
    monkeypatch.setattr(containers, "license_path", lambda root=None: tmp_path / "license.txt")
    calls: list[tuple[Path, str, bool]] = []

    def fake_apptainer_pull(target, uri, *, dry_run=False):
        calls.append((target, uri, dry_run))

    monkeypatch.setattr(containers, "_run_apptainer_pull", fake_apptainer_pull)

    containers.install_container("freesurfer", source="upstream", dry_run=True)

    assert calls == [
        (
            tmp_path / "containers" / "neurocontainer" / "freesurfer_8.1.0_20260311" / "freesurfer_8.1.0_20260311.simg",
            "docker://vnmd/freesurfer_8.1.0:20260311",
            True,
        )
    ]


def test_refresh_index_scans_generic_neurocontainer(tmp_path: Path, monkeypatch):
    """Verify refresh index scans non-core NeuroContainer images."""
    root = tmp_path
    container_root = root / ".apptainer" / "containers"
    image = container_root / "neurocontainer" / "matlab_R2023b_20250101" / "matlab_R2023b_20250101.simg"
    image.parent.mkdir(parents=True)
    image.write_text("fake image", encoding="utf-8")
    monkeypatch.setenv("NEUROCADE_CONTAINER_ROOT", str(container_root))
    monkeypatch.setenv("TOOL_CATALOG_DIR", str(root / "llm-data" / "tool-catalog"))
    monkeypatch.delenv("NEUROCADE_CONTAINER_INVENTORY", raising=False)
    monkeypatch.delenv("NEUROCADE_INSTALLED_TOOLS_JSONL", raising=False)

    refresh_index(root=root, harvest_help=False)

    inventory_path = root / "llm-data" / "tool-catalog" / "installed_containers.json"
    tools_path = root / "llm-data" / "tool-catalog" / "installed_tools.jsonl"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    tool_rows = [json.loads(line) for line in tools_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert inventory["containers"][0]["name"] == "matlab"
    assert inventory["containers"][0]["runtime_version"] == "R2023b"
    assert inventory["containers"][0]["build_date"] == "20250101"
    assert tool_rows[0]["name"] == "matlab"
    assert tool_rows[0]["image_path"] == str(image.resolve())
