"""Isolated container probing for assistant-authored workflow definitions."""

from __future__ import annotations

import re
import shlex
from typing import Any, Literal

from neurocade_runtime_tools.container_request import build_container_request
from neurocade_runtime_tools.execution import RuntimeExecutionRequest, execute_runtime_request_async
from pydantic import BaseModel, Field, field_validator, model_validator

from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from api_service.assistant.tools.registration import ToolRegistration
from api_service.runtime_tools.runtime_images import runtime_image_spec

PROBE_TIMEOUT_SECONDS = 20.0
PROBE_MAX_STREAM_CHARS = 32_768
PROBE_MAX_PROCESSES = 64
PROBE_MAX_VIRTUAL_MEMORY_KIB = 1024 * 1024
_TAGGED_IMAGE_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]*"
)


class ToolProbeArgs(BaseModel):
    """Model-controlled fields for one disposable container probe."""

    image: str = Field(
        ...,
        description=(
            "Exact explicitly tagged workflow image returned by tool_image_search or used by a configured workflow."
        ),
    )
    script: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description=(
            "Short Bash probe, normally command -v, --help, or --version. No case or workspace files are mounted."
        ),
    )

    model_config = {"extra": "forbid"}

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        cleaned = value.strip()
        if not _TAGGED_IMAGE_PATTERN.fullmatch(cleaned):
            raise ValueError("image must be a valid container image name with an explicit tag")
        if cleaned.rsplit(":", 1)[1].lower() == "latest":
            raise ValueError("image must use an explicit non-latest tag")
        return cleaned


class ToolInspectArgs(BaseModel):
    """Structured inspection; no shell fragments or case paths are accepted."""

    model_config = {"extra": "forbid"}
    tool_id: str | None = Field(default=None, min_length=1, description="Inspect a configured workflow by ID instead of a CLI.")
    image: str | None = None
    executable: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_./+-]+$")
    action: Literal["search", "locate", "help", "version", "source"] = "help"
    query: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.+-]+$",
        description="Case-insensitive literal executable-name substring for search; scans absolute PATH directories, returns at most 50 matches.")
    arguments: list[str] | None = Field(default=None, max_length=8,
        description="Optional literal help/version arguments, e.g. ['-h']. Never shell syntax.")
    start_line: int = Field(default=1, ge=1, le=100000)
    line_count: int = Field(default=120, ge=1, le=200)

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        return ToolProbeArgs.validate_image(value) if value is not None else None

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(len(arg) > 256 or "\x00" in arg for arg in value):
            raise ValueError("Inspection arguments must be short literal strings")
        return value

    @model_validator(mode="after")
    def validate_target(self):
        if self.tool_id:
            if self.image is not None or self.executable is not None or self.arguments is not None or self.query is not None or self.action != "help":
                raise ValueError("Use tool_id alone, or image and executable for CLI inspection")
        elif self.action == "search":
            if not self.image or not self.query or self.executable is not None or self.arguments is not None:
                raise ValueError("Search requires image and query only")
        elif self.query is not None:
            raise ValueError("query is only supported for search")
        elif not self.image or not self.executable:
            raise ValueError("CLI inspection requires image and executable")
        return self


def inspection_script(parsed: ToolInspectArgs) -> str:
    if parsed.action == "search":
        assert parsed.query is not None
        return (
            f"query={shlex.quote(parsed.query)}\n"
            'shopt -s nullglob nocasematch\n'
            'IFS=: read -r -a dirs <<< "$PATH"\n'
            'count=0\n'
            'for dir in "${dirs[@]}"; do\n'
            '  [[ "$dir" == /* && -d "$dir" ]] || continue\n'
            '  for file in "$dir"/*; do\n'
            '    [[ -f "$file" && -x "$file" ]] || continue\n'
            '    name=${file##*/}\n'
            '    [[ "$name" == *"$query"* ]] || continue\n'
            '    if (( count == 50 )); then echo "Results truncated at 50 matches; narrow query."; exit 0; fi\n'
            '    printf "%s\\n" "$file"\n'
            '    count=$((count + 1))\n'
            '  done\n'
            'done\n'
            'if (( count == 0 )); then echo "No executable names matched in absolute PATH directories."; fi\n'
        )
    assert parsed.executable is not None
    executable = shlex.quote(parsed.executable)
    locate = f'EXE=$(command -v -- {executable}) || {{ echo "Executable not on PATH; use action=search with a distinctive query, or supply its absolute image path." >&2; exit 127; }}\n'
    if parsed.action == "locate":
        return locate + 'printf "%s\\n" "$EXE"'
    if parsed.action == "source":
        return locate + ('LC_ALL=C grep -Iq . "$EXE" || { echo "Source inspection requires a text wrapper, not a compiled binary." >&2; exit 65; }\n'
                         + f'sed -n {parsed.start_line},{parsed.start_line + parsed.line_count - 1}p -- "$EXE" | head -c 65536')
    arguments = parsed.arguments if parsed.arguments is not None else ["--" + parsed.action]
    return locate + 'exec "$EXE" ' + shlex.join(arguments)


def _runtime_image(image: str) -> str:
    """Apply the catalog's Neurodesk shorthand without changing explicit registries."""
    return image if "/" in image else f"vnmd/{image}"


def _bounded(value: str) -> tuple[str, bool]:
    if len(value) <= PROBE_MAX_STREAM_CHARS:
        return value, False
    marker = f"\n...[truncated {len(value) - PROBE_MAX_STREAM_CHARS} characters]...\n"
    remaining = PROBE_MAX_STREAM_CHARS - len(marker)
    head = remaining // 2
    return value[:head] + marker + value[-(remaining - head) :], True


def _limited_script(script: str) -> str:
    """Lower inherited soft and hard limits before executing model-provided Bash."""
    cpu_seconds = int(PROBE_TIMEOUT_SECONDS)
    return (
        f"ulimit -Su {PROBE_MAX_PROCESSES}; ulimit -Hu {PROBE_MAX_PROCESSES}; "
        f"ulimit -Sv {PROBE_MAX_VIRTUAL_MEMORY_KIB}; ulimit -Hv {PROBE_MAX_VIRTUAL_MEMORY_KIB}; "
        f"ulimit -St {cpu_seconds}; ulimit -Ht {cpu_seconds}; "
        + script
    )


class AssistantProbeTools:
    """Build the two isolated authoring tools; legacy probe method is not advertised."""

    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        from api_service.assistant.tools.workflow_test_tools import AssistantWorkflowTestTools

        registration = ToolRegistration(
            "tool_inspect",
            "Inspect a configured workflow with tool_id, search executable names with image/action=search/query, "
            "OR inspect a CLI with image and executable: locate/help/version/source. "
            "Search uses a case-insensitive literal substring in absolute PATH directories (maximum 50 matches); it never executes matches. "
            "No current case, workspace, host files or credentials are mounted. Network disabled. "
            "Arguments are literal CLI arguments, not shell. Nonzero help exit codes may be normal; read evidence. "
            "Use tool_workflow_test for supported staging tests, not hand-written probe scripts.",
            ToolInspectArgs.model_json_schema(), self.inspect, parallel_safe=False,
        )
        return [registration.bind(state), *AssistantWorkflowTestTools(settings=self.settings).build_tools(state)]

    async def inspect(self, state: dict[str, Any], execution: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            parsed = ToolInspectArgs.model_validate(arguments)
        except ValueError as exc:
            return ToolResult.error(f"Invalid inspection request: {exc}")
        if parsed.tool_id:
            from api_service.assistant.tools.catalog_execution import AssistantCatalogExecutor
            from api_service.assistant.tools.catalog_tools import AssistantCatalogTools
            return AssistantCatalogTools(AssistantCatalogExecutor(settings=self.settings)).inspect(
                state, execution, {"tool_id": parsed.tool_id})
        result = await self.probe(state, execution, {"image": parsed.image, "script": inspection_script(parsed)})
        if result.details:
            payload = {**result.details, "action": parsed.action, "executable": parsed.executable, "query": parsed.query,
                       "status": "completed" if not result.is_error else "command_failed",
                       "assertions": "not_run", "validation_scope": "CLI inspection only; no workflow was tested.",
                       "guidance": "Read stdout, stderr and return_code. A help command may use a nonzero exit code. "
                       "After two equivalent failures, preserve a draft and state what remains unverified."}
            return ToolResult.structured(payload, is_error=result.is_error)
        return result

    async def probe(
        self,
        state: dict[str, Any],
        _execution: ToolExecutionContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        try:
            parsed = ToolProbeArgs.model_validate(arguments)
            image = _runtime_image(parsed.image)
            container_run = build_container_request(
                image=runtime_image_spec(image),
                command=["bash", "-lc", _limited_script(parsed.script)],
                env={"TMPDIR": "/tmp", "LC_ALL": "C", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
                disable_network=True,
                gpu=False,
            )
            container_run.isolated = True

            async def report_progress(progress: dict[str, Any]) -> None:
                event_sink = state.get("event_sink")
                if event_sink is None:
                    return
                if progress.get("phase") == "ready":
                    await event_sink(
                        "activity",
                        {"kind": "tool", "label": "tool_inspect", "blocking": True},
                    )
                    return
                await event_sink(
                    "activity",
                    {
                        "kind": "image",
                        "label": parsed.image,
                        "blocking": True,
                        "phase": progress.get("phase"),
                        "progress": progress.get("progress"),
                        "completed_layers": progress.get("completed_layers"),
                        "total_layers": progress.get("total_layers"),
                        "current_bytes": progress.get("current_bytes"),
                        "total_bytes": progress.get("total_bytes"),
                        "disk_free_bytes": progress.get("disk_free_bytes"),
                        "disk_warning": progress.get("disk_warning"),
                        "reclaimable_storage": progress.get("reclaimable_storage"),
                        "stalled_seconds": progress.get("stalled_seconds"),
                        "process_active": progress.get("process_active"),
                    },
                )

            result = await execute_runtime_request_async(
                RuntimeExecutionRequest(
                    timeout_s=PROBE_TIMEOUT_SECONDS,
                    execution_mode="isolated-tool-probe",
                    container_run=container_run,
                ),
                progress_observer=report_progress,
            )
        except Exception as exc:
            return ToolResult.error(f"Error probing tool image: {exc}")

        stdout, stdout_truncated = _bounded(result.stdout)
        stderr, stderr_truncated = _bounded(result.stderr)
        payload = {
            "image": parsed.image,
            "return_code": result.returncode,
            "status": "completed" if result.returncode == 0 else "command_failed",
            "validation_scope": "Only the submitted script and its assertions in an isolated container.",
            "not_verified": ["case inputs", "real bind-mount aliases", "licenses", "full workflow execution", "scientific validity"],
            "guidance": "A zero exit code does not prove correctness of printed listings. Assert expected results and absence of "
            "recursive output copies. Symlinks are not substitutes for bind mounts; realpath does not collapse bind aliases. "
            "Use a processing stub for synthetic tests: /tmp is noexec and resource limits are lower than workflow execution.",
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "sandbox": {
                "workspace_mounted": False,
                "network_disabled": True,
                "root_filesystem_read_only": True,
                "ephemeral_tmp": True,
                "timeout_s": PROBE_TIMEOUT_SECONDS,
                "max_processes": PROBE_MAX_PROCESSES,
                "max_virtual_memory_mib": PROBE_MAX_VIRTUAL_MEMORY_KIB // 1024,
            },
        }
        return ToolResult.structured(payload, is_error=result.returncode != 0)
