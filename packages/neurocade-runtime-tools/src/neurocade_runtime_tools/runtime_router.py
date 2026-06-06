"""Route installed NeuroCade runtime tools through Apptainer."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

from neurocade_runtime_tools.containers import missing_container_message
from neurocade_runtime_tools.execution import RuntimeExecutionPolicy, RuntimeExecutionRequest, execute_runtime_request
from neurocade_runtime_tools.apptainer_command import RuntimeBind, build_apptainer_exec_command

DEFAULT_RECORDS_JSONL = Path("llm-data/tool-catalog/installed_tools.jsonl")
DEFAULT_BIN_DIR = Path("bin/neurocontainers")
_VALID_SHIM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


class RouterError(RuntimeError):
    pass


def _load_rows(records_jsonl: Path) -> list[dict]:
    """Read installed tool records from a JSONL catalog."""
    return [
        json.loads(line)
        for line in records_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _lookup_map(records_jsonl: Path, rows: list[dict] | None = None) -> dict[str, dict]:
    """Map tool names and unambiguous aliases to their catalog rows."""
    lookup: dict[str, dict] = {}
    protected_names: set[str] = set()
    ambiguous_aliases: set[str] = set()
    rows = rows if rows is not None else _load_rows(records_jsonl)
    for row in rows:
        name = row["name"]
        protected_names.add(name)
        lookup[name] = row
    for row in rows:
        for alias in row.get("aliases") or []:
            if not alias or alias in protected_names:
                continue
            existing = lookup.get(alias)
            if existing and existing["name"] != row["name"]:
                ambiguous_aliases.add(alias)
                continue
            lookup[alias] = row
    for alias in ambiguous_aliases:
        if alias not in protected_names:
            lookup.pop(alias, None)
    return lookup


def _format_tool_suggestion(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "").strip()
    command = str(row.get("container_command") or "").strip()
    app = str(row.get("app") or row.get("toolbox") or "").strip()
    details = []
    if command and command != name:
        details.append(f"command: {command}")
    if app:
        details.append(f"app: {app}")
    return f"{name} ({', '.join(details)})" if details else name


def _tool_suggestions(tool: str, rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: dict[str, Any]) -> None:
        name = str(row.get("name") or "").strip()
        if not name or name in seen:
            return
        suggestions.append(row)
        seen.add(name)

    try:
        from neurocade_runtime_tools.retrieval import hybrid_rank

        for hit in hybrid_rank(tool, rows, n_results=limit * 2):
            if float(hit.get("score") or 0.0) <= 0.0:
                continue
            add(hit)
            if len(suggestions) >= limit:
                return suggestions
    except Exception:
        pass

    keyed_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = [row.get("name"), row.get("container_command")]
        values.extend(row.get("aliases") or [])
        for value in values:
            key = str(value or "").strip()
            if key:
                keyed_rows.setdefault(key, row)
    for match in difflib.get_close_matches(tool, list(keyed_rows), n=limit * 2, cutoff=0.45):
        add(keyed_rows[match])
        if len(suggestions) >= limit:
            break
    return suggestions


def _tool_not_found_message(tool: str, records_jsonl: Path, rows: list[dict[str, Any]]) -> str:
    message = [
        f"Tool '{tool}' was not found in {records_jsonl}.",
        (
            "tool_call requires an exact installed catalog name or unambiguous alias; "
            "it does not infer alternate command names."
        ),
    ]
    suggestions = _tool_suggestions(tool, rows)
    if suggestions:
        message.append(
            "Closest installed tools: "
            + "; ".join(_format_tool_suggestion(row) for row in suggestions)
            + "."
        )
    else:
        message.append(f"No close installed tools matched among {len(rows)} indexed tool row(s).")
    message.append(
        "Run tool_search first and pass the exact `name` it returns to tool_call. "
        "If the expected tool is missing, refresh the catalog with `./scripts/containers.sh refresh-index`."
    )
    return " ".join(message)


def resolve_tool(tool: str, *, records_jsonl: Path = DEFAULT_RECORDS_JSONL) -> dict:
    """Return metadata for an installed tool or alias."""
    rows = _load_rows(records_jsonl)
    lookup = _lookup_map(records_jsonl, rows)
    row = lookup.get(tool)
    if row is None:
        raise RouterError(_tool_not_found_message(tool, records_jsonl, rows))
    for key in ("image_path", "container_command"):
        if not row.get(key):
            raise RouterError(f"Tool '{tool}' is missing required field '{key}'.")
    return row


def docker_image_uri(row: dict) -> str:
    """Build the NeuroContainers Docker URI for a catalog row."""
    app = row["app"]
    version = row["runtime_version"]
    build_date = row.get("build_date")
    if not build_date:
        raise RouterError(f"Cannot derive NeuroContainers URI for {row['name']}: missing build_date.")
    return f"docker://vnmd/{str(app).lower()}_{version}:{build_date}"


def ensure_image_exists(row: dict) -> Path:
    """Return the container image path after verifying it exists."""
    image_path = Path(row["image_path"])
    if not image_path.exists():
        name = str(row.get("app") or row.get("toolbox") or row.get("name") or "tool")
        raise RouterError(f"{missing_container_message(name, stale_index=True)} Missing image path: {image_path}.")
    return image_path


def build_container_command(
    row: dict,
    tool_args: list[str],
    *,
    project_root: Path | None = None,
    binds: Sequence[RuntimeBind] = (),
) -> list[str]:
    """Build the Apptainer command that invokes a runtime tool."""
    image_path = Path(row["image_path"])
    has_case_bind = any(bind.container_path.rstrip("/") == "/case" for bind in binds)
    has_workspace_bind = any(bind.container_path.rstrip("/") == "/workspace" for bind in binds)
    return build_apptainer_exec_command(
        runtime_bin=os.environ.get("APPTAINER_BIN", "apptainer"),
        image=image_path,
        command=[row["container_command"], *tool_args],
        binds=binds,
        cwd="/case" if has_case_bind else "/workspace" if has_workspace_bind else project_root,
        disable_network=True,
        no_mounts=("cwd",) if has_case_bind or has_workspace_bind else (),
        cleanenv=True,
        no_home=True,
    )


def build_runtime_command(
    tool: str,
    tool_args: list[str],
    *,
    records_jsonl: Path = DEFAULT_RECORDS_JSONL,
    project_root: Path | None = None,
    binds: Sequence[RuntimeBind] = (),
) -> list[str]:
    """Resolve a tool and build its Apptainer invocation."""
    row = resolve_tool(tool, records_jsonl=records_jsonl)
    return build_container_command(
        row,
        tool_args,
        project_root=(project_root or Path.cwd()).resolve(),
        binds=binds,
    )


def run_tool(
    tool: str,
    tool_args: list[str],
    *,
    records_jsonl: Path = DEFAULT_RECORDS_JSONL,
    project_root: Path | None = None,
    binds: Sequence[RuntimeBind] = (),
) -> int:
    """Execute an installed runtime tool through Apptainer."""
    row = resolve_tool(tool, records_jsonl=records_jsonl)
    ensure_image_exists(row)
    command = build_container_command(
        row,
        tool_args,
        project_root=(project_root or Path.cwd()).resolve(),
        binds=binds,
    )
    result = execute_runtime_request(
        RuntimeExecutionRequest(
            argv=command,
            cwd=(project_root or Path.cwd()).resolve(),
            timeout_s=None,
            execution_mode="runtime-router-subprocess",
            require_rootless_apptainer=True,
            runtime_policy=RuntimeExecutionPolicy(network_disabled=True, gpu_enabled=False),
            capture_output=False,
        )
    )
    return result.returncode


def _iter_unique_tool_names(records_jsonl: Path):
    """Yield unique shim-safe tool names and aliases from the catalog."""
    seen: set[str] = set()
    for row in _load_rows(records_jsonl):
        for key in [row["name"], *(row.get("aliases") or [])]:
            if not key or key in seen or not _VALID_SHIM_NAME.match(key):
                continue
            seen.add(key)
            yield key


def install_shims(
    *,
    records_jsonl: Path = DEFAULT_RECORDS_JSONL,
    bin_dir: Path = DEFAULT_BIN_DIR,
) -> list[Path]:
    """Create executable shims for indexed runtime tools."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    router_target = Path(__file__).resolve()
    created: list[Path] = []
    for tool_name in _iter_unique_tool_names(records_jsonl):
        shim_path = bin_dir / tool_name
        script = (
            "#!/bin/sh\n"
            "exec python3 "
            f"{shlex.quote(str(router_target))} run --records-jsonl "
            f"{shlex.quote(str(records_jsonl.resolve()))} {shlex.quote(tool_name)} -- \"$@\"\n"
        )
        shim_path.write_text(script, encoding="utf-8")
        shim_path.chmod(
            shim_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        created.append(shim_path)
    return created


def _infer_invoked_tool(argv0: str) -> str | None:
    """Infer a shim-invoked tool name from argv[0]."""
    name = Path(argv0).name
    if name in {"runtime_router.py", "neurocade-runtime-tool", "python", "python3"}:
        return None
    return name


def main() -> None:
    """Route CLI commands or shim invocations to runtime tools."""
    invoked_tool = _infer_invoked_tool(sys.argv[0])
    if invoked_tool:
        raise SystemExit(run_tool(invoked_tool, sys.argv[1:]))

    parser = argparse.ArgumentParser(description="Route installed container tools through Apptainer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an installed runtime tool")
    run_parser.add_argument("--records-jsonl", type=Path, default=DEFAULT_RECORDS_JSONL)
    run_parser.add_argument("tool")
    run_parser.add_argument("args", nargs=argparse.REMAINDER)

    install_parser = subparsers.add_parser(
        "install-shims", help="Create one shim per indexed tool"
    )
    install_parser.add_argument(
        "--records-jsonl", type=Path, default=DEFAULT_RECORDS_JSONL
    )
    install_parser.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)

    resolve_parser = subparsers.add_parser("resolve", help="Show installed tool row metadata")
    resolve_parser.add_argument("tool")
    resolve_parser.add_argument(
        "--records-jsonl", type=Path, default=DEFAULT_RECORDS_JSONL
    )

    args = parser.parse_args()
    project_root = Path.cwd().resolve()

    if args.command == "run":
        tool_args = list(args.args)
        if tool_args and tool_args[0] == "--":
            tool_args = tool_args[1:]
        raise SystemExit(
            run_tool(args.tool, tool_args, records_jsonl=args.records_jsonl, project_root=project_root)
        )

    if args.command == "install-shims":
        created = install_shims(records_jsonl=args.records_jsonl, bin_dir=args.bin_dir)
        print(f"created {len(created)} shims in {args.bin_dir}")
        return

    if args.command == "resolve":
        row = resolve_tool(args.tool, records_jsonl=args.records_jsonl)
        print(
            json.dumps(
                {
                    "tool": row["name"],
                    "toolbox": row["toolbox"],
                    "app": row["app"],
                    "runtime_version": row["runtime_version"],
                    "build_date": row.get("build_date"),
                    "image_path": row["image_path"],
                    "container_command": row["container_command"],
                },
                indent=2,
            )
        )
        return


if __name__ == "__main__":
    main()
