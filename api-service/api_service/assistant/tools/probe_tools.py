"""Isolated container probing for assistant-authored workflow definitions."""

from __future__ import annotations

import json
import re
from typing import Any

from neurocade_runtime_tools.container_request import build_container_request
from neurocade_runtime_tools.execution import RuntimeExecutionRequest, execute_runtime_request_async
from pydantic import BaseModel, Field, field_validator

from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from api_service.assistant.tools.registration import ToolRegistration
from api_service.runtime_tools.runtime_images import runtime_image_spec

PROBE_TIMEOUT_SECONDS = 20.0
PROBE_MAX_STREAM_CHARS = 32_768
PROBE_MAX_PROCESSES = 64
PROBE_MAX_VIRTUAL_MEMORY_KIB = 512 * 1024
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
    """Build and execute the read-only ``tool_probe`` assistant tool."""

    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        registration = ToolRegistration(
            "tool_probe",
            (
                "Run a short Bash probe in an isolated, network-disabled tool image, downloading it on first use. "
                "Use this before adding an unfamiliar command-line utility to the private workflow catalog. "
                "No case, workspace, user configuration, credentials, or host files are mounted; only ephemeral "
                "/tmp is writable. Prefer command -v plus --help or --version. If command -v fails, search common "
                "executable roots such as /opt and /usr/local before concluding that the utility is absent."
            ),
            ToolProbeArgs.model_json_schema(),
            self.probe,
            parallel_safe=False,
        )
        return [registration.bind(state)]

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
                env={"TMPDIR": "/tmp", "LC_ALL": "C"},
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
                        {"kind": "tool", "label": "tool_probe", "blocking": True},
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
        return ToolResult.success(json.dumps(payload, indent=2), details=payload)
