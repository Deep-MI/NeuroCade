"""Isolated container probing for assistant-authored workflow definitions."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from neurocade_runtime_tools.container_request import build_container_request
from neurocade_runtime_tools.execution import RuntimeExecutionRequest, execute_runtime_request
from pydantic import BaseModel, Field, field_validator

from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from api_service.assistant.tools.registration import ToolRegistration
from api_service.runtime_tools.neurodesk_images import resolve_or_prepare_image

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
        _state: dict[str, Any],
        _execution: ToolExecutionContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        try:
            parsed = ToolProbeArgs.model_validate(arguments)
            image = _runtime_image(parsed.image)
            runtime_image = await asyncio.to_thread(resolve_or_prepare_image, image, settings=self.settings)
            container_run = build_container_request(
                image=runtime_image,
                command=["bash", "-lc", _limited_script(parsed.script)],
                env={"TMPDIR": "/tmp", "LC_ALL": "C"},
                disable_network=True,
                gpu=False,
            )
            container_run.isolated = True
            result = await asyncio.to_thread(
                execute_runtime_request,
                RuntimeExecutionRequest(
                    argv=[],
                    timeout_s=PROBE_TIMEOUT_SECONDS,
                    execution_mode="isolated-tool-probe",
                    container_run=container_run,
                ),
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
