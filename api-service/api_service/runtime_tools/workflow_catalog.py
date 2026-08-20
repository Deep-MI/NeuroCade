"""Load the authoritative neuroimaging workflow catalog."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend_common.settings import ROOT_DIR

WORKFLOW_CATALOG_PATH = ROOT_DIR / "config" / "neuroimaging_tools.yaml"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_RUN_ID_TOKEN = "{run_id}"
_RUNTIME_VARIABLE_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?:\[(\d+)\])?\}")
_RUNTIME_VARIABLES = {"CASE_ROOT", "DEVICE", "INPUTS", "OUTPUTS", "RUN_DIR"}
_VALID_RETURN_FIELDS = ("return_code", "stdout", "stderr", "outputs")
_USER_CATALOG_DIRECTORY = ".user-tool-configs"
_USER_CATALOG_LOCK = threading.Lock()


class StrictWorkflowModel(BaseModel):
    """Reject misspelled or unsupported workflow configuration fields."""

    model_config = ConfigDict(extra="forbid")


class WorkflowInput(StrictWorkflowModel):
    """One ordered regular-file input accepted by a workflow."""

    name: str = Field(pattern=_ID_PATTERN)
    description: str


class WorkflowOutput(StrictWorkflowModel):
    """One typed file output produced by a workflow."""

    name: str = Field(pattern=_ID_PATTERN)
    type: Literal["intensity_volume", "segmentation_volume", "surface", "other"]
    path: str = Field(
        description=(
            "Normalized relative path beneath the active case/workspace, for example "
            "mri/segmentation.mgz. Never prefix it with /case, /workspace, or a slash."
        )
    )
    description: str
    required: bool = True
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Viewer metadata. For a FreeSurfer-LUT segmentation use "
            '{"lut": "freesurfer", "visible": true}.'
        ),
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned.endswith("/"):
            raise ValueError("output path must be normalized without a trailing slash")
        if cleaned == ".":
            raise ValueError("output path must name a file")
        substituted = cleaned.replace(_RUN_ID_TOKEN, "run-id")
        path = PurePosixPath(substituted)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError("output path must be a normalized relative path")
        if path.parts[0] == ".runs":
            raise ValueError("output path must not use the reserved .runs workspace")
        if "{" in substituted or "}" in substituted:
            raise ValueError("output path contains an unsupported template variable")
        return cleaned


class WorkflowExecution(StrictWorkflowModel):
    """Workflow scheduling and resource policy."""

    mode: Literal["synchronous", "background"] = Field(
        default="synchronous",
        description="Use background only for long-running jobs or Run Analysis workflows.",
    )
    gpu: bool = True
    timeout_s: float | None = Field(default=None, gt=0)
    queue: str = Field(default="api", pattern=_ID_PATTERN)


class WorkflowReturn(StrictWorkflowModel):
    """Select the bounded execution fields returned to the model."""

    include: list[Literal["return_code", "stdout", "stderr", "outputs"]] = Field(
        default_factory=lambda: list(_VALID_RETURN_FIELDS)
    )
    max_stream_chars: int = Field(default=16_384, ge=256, le=1_000_000)

    @field_validator("include")
    @classmethod
    def validate_unique_fields(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("return.include contains duplicate fields")
        return value


class WorkflowUi(StrictWorkflowModel):
    """Optional presentation metadata for the Run Analysis menu."""

    label: str | None = None


class NeuroimagingWorkflow(StrictWorkflowModel):
    """One fixed, trusted Bash workflow executed in a Neurodesk image."""

    id: str = Field(pattern=_ID_PATTERN)
    image: str
    description: str
    details: str
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
    script: str = Field(
        description=(
            "Bash script. Available runtime variables are ${INPUTS[n]}, ${OUTPUTS[n]}, "
            "${RUN_DIR}, ${CASE_ROOT}, and ${DEVICE}. Write declared files to their "
            "${OUTPUTS[n]} paths. Every runtime path reference must be directly enclosed in "
            "double quotes, for example command \"${INPUTS[0]}\" \"${OUTPUTS[0]}\". "
            "Output {run_id} templates are resolved before execution; "
            "${RUN_ID} is not available."
        )
    )
    execution: WorkflowExecution = Field(default_factory=WorkflowExecution)
    return_policy: WorkflowReturn = Field(
        default_factory=WorkflowReturn,
        alias="return",
        serialization_alias="return",
    )
    ui: WorkflowUi = Field(default_factory=WorkflowUi)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        cleaned = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]*", cleaned):
            raise ValueError("image must be a valid container image name with an explicit tag")
        if ":" not in cleaned or cleaned.rsplit(":", 1)[1].lower() == "latest":
            raise ValueError("image must use an explicit non-latest tag")
        return cleaned

    @field_validator("description", "details", "script")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be empty")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> NeuroimagingWorkflow:
        duplicate_inputs = _duplicates(item.name for item in self.inputs)
        duplicate_outputs = _duplicates(item.name for item in self.outputs)
        duplicate_paths = _duplicates(item.path for item in self.outputs)
        if duplicate_inputs:
            raise ValueError(f"duplicate input name(s): {', '.join(duplicate_inputs)}")
        if duplicate_outputs:
            raise ValueError(f"duplicate output name(s): {', '.join(duplicate_outputs)}")
        if duplicate_paths:
            raise ValueError(f"duplicate output path(s): {', '.join(duplicate_paths)}")
        try:
            syntax_check = subprocess.run(
                ["bash", "-n"],
                input=self.script,
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ValueError("bash is required to validate workflow scripts") from exc
        if syntax_check.returncode != 0:
            raise ValueError(f"script is invalid Bash: {syntax_check.stderr.strip()}")
        for label, count, pattern in (
            ("input", len(self.inputs), re.compile(r"\$\{INPUTS\[(\d+)\]\}")),
            ("output", len(self.outputs), re.compile(r"\$\{OUTPUTS\[(\d+)\]\}")),
        ):
            invalid = sorted({int(match) for match in pattern.findall(self.script) if int(match) >= count})
            if invalid:
                raise ValueError(f"script references undeclared {label} index(es): {invalid}")
            unquoted = [
                match.group(0)
                for match in pattern.finditer(self.script)
                if not _match_is_in_comment(self.script, match.start())
                and not _match_is_double_quoted(self.script, match.start(), match.end())
            ]
            if unquoted:
                raise ValueError(f"script must double-quote runtime path reference {unquoted[0]}")
        unsupported_runtime_variables = sorted(
            {
                name
                for name, _index in _RUNTIME_VARIABLE_PATTERN.findall(self.script)
                if name not in _RUNTIME_VARIABLES
            }
        )
        if unsupported_runtime_variables:
            rendered = ", ".join(f"${{{name}}}" for name in unsupported_runtime_variables)
            raise ValueError(f"script references unsupported runtime variable(s): {rendered}")
        return self

    @property
    def label(self) -> str:
        return self.ui.label or self.id.replace("_", " ").title()

    @property
    def neurodesk_image(self) -> str:
        return self.image if "/" in self.image else f"vnmd/{self.image}"


class WorkflowCatalog(StrictWorkflowModel):
    """Validated root document for the one authoritative catalog."""

    version: Literal[1]
    tools: list[NeuroimagingWorkflow]

    @model_validator(mode="after")
    def validate_tools(self) -> WorkflowCatalog:
        duplicates = _duplicates(tool.id for tool in self.tools)
        if duplicates:
            raise ValueError(f"duplicate workflow id(s): {', '.join(duplicates)}")
        return self


def _duplicates(values) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _match_is_in_comment(script: str, start: int) -> bool:
    line_start = script.rfind("\n", 0, start) + 1
    return script[line_start:start].lstrip().startswith("#")


def _match_is_double_quoted(script: str, start: int, end: int) -> bool:
    return start > 0 and end < len(script) and script[start - 1] == '"' and script[end] == '"'


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text)}


def _read_workflow_catalog(path: Path) -> WorkflowCatalog:
    """Read and validate one workflow catalog without caching it."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Neuroimaging workflow catalog was not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Neuroimaging workflow catalog is invalid YAML: {path}") from exc
    try:
        return WorkflowCatalog.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Neuroimaging workflow catalog is invalid: {exc}") from exc


@lru_cache(maxsize=1)
def load_workflow_catalog(path: Path = WORKFLOW_CATALOG_PATH) -> WorkflowCatalog:
    """Return the validated authoritative built-in workflow catalog."""
    return _read_workflow_catalog(path)


def user_workflow_catalog_path(settings: Any, user_id: str) -> Path:
    """Return a filesystem-safe private catalog path for one authenticated user."""
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return settings.outputs_dir / _USER_CATALOG_DIRECTORY / f"{digest}.yaml"


def load_user_workflow_catalog(settings: Any, user_id: str) -> WorkflowCatalog:
    """Load one user's uncached workflow overlay, returning an empty overlay when absent."""
    path = user_workflow_catalog_path(settings, user_id)
    if not path.exists():
        return WorkflowCatalog(version=1, tools=[])
    return _read_workflow_catalog(path)


def _merge_workflow_catalogs(base: WorkflowCatalog, overlay: WorkflowCatalog) -> WorkflowCatalog:
    """Apply private definitions over built-ins while preserving catalog order."""
    overrides = {tool.id: tool for tool in overlay.tools}
    merged = [overrides.pop(tool.id, tool) for tool in base.tools]
    merged.extend(overrides.values())
    return WorkflowCatalog(version=base.version, tools=merged)


def effective_workflow_catalog(*, settings: Any | None = None, user_id: str | None = None) -> WorkflowCatalog:
    """Merge the built-in catalog with one user's private overrides."""
    base = load_workflow_catalog()
    if settings is None or not user_id:
        return base
    overlay = load_user_workflow_catalog(settings, user_id)
    return _merge_workflow_catalogs(base, overlay)


def _catalog_payload(catalog: WorkflowCatalog) -> dict[str, Any]:
    return catalog.model_dump(mode="json", by_alias=True, exclude_none=True)


def _atomic_write_catalog(path: Path, catalog: WorkflowCatalog) -> None:
    """Atomically replace a user overlay with a validated YAML document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(_catalog_payload(catalog), sort_keys=False, allow_unicode=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def upsert_user_workflow(settings: Any, user_id: str, definition: dict[str, Any]) -> NeuroimagingWorkflow:
    """Create or replace one workflow in a user's overlay and reload it immediately."""
    tool = NeuroimagingWorkflow.model_validate(definition)
    path = user_workflow_catalog_path(settings, user_id)
    with _USER_CATALOG_LOCK:
        overlay = load_user_workflow_catalog(settings, user_id)
        tools = [existing for existing in overlay.tools if existing.id != tool.id]
        tools.append(tool)
        updated = WorkflowCatalog(version=1, tools=tools)
        # Validate the merged view before changing the file.
        _merge_workflow_catalogs(load_workflow_catalog(), updated)
        _atomic_write_catalog(path, updated)
        return next(item for item in load_user_workflow_catalog(settings, user_id).tools if item.id == tool.id)


def delete_user_workflow(settings: Any, user_id: str, tool_id: str) -> NeuroimagingWorkflow:
    """Delete one private workflow or override and reload the overlay immediately."""
    path = user_workflow_catalog_path(settings, user_id)
    with _USER_CATALOG_LOCK:
        overlay = load_user_workflow_catalog(settings, user_id)
        removed = next((tool for tool in overlay.tools if tool.id == tool_id), None)
        if removed is None:
            raise ValueError(f"User workflow {tool_id!r} was not found.")
        updated = WorkflowCatalog(version=1, tools=[tool for tool in overlay.tools if tool.id != tool_id])
        _atomic_write_catalog(path, updated)
        load_user_workflow_catalog(settings, user_id)
        return removed


def workflow_source(tool_id: str, *, settings: Any | None = None, user_id: str | None = None) -> str:
    """Describe whether an effective workflow is built-in, private, or a private override."""
    base_ids = {tool.id for tool in load_workflow_catalog().tools}
    if settings is not None and user_id:
        user_ids = {tool.id for tool in load_user_workflow_catalog(settings, user_id).tools}
        if tool_id in user_ids:
            return "user_override" if tool_id in base_ids else "user"
    return "built_in"


def workflows(*, settings: Any | None = None, user_id: str | None = None) -> list[NeuroimagingWorkflow]:
    return list(effective_workflow_catalog(settings=settings, user_id=user_id).tools)


def search_workflows(
    query: str,
    *,
    top_k: int = 5,
    settings: Any | None = None,
    user_id: str | None = None,
) -> list[tuple[NeuroimagingWorkflow, float]]:
    """Rank catalog workflows with a small lexical scorer."""
    query_tokens = _tokens(query)
    rows: list[tuple[NeuroimagingWorkflow, float]] = []
    for tool in workflows(settings=settings, user_id=user_id):
        text_tokens = _tokens(" ".join((tool.id, tool.label, tool.description)))
        overlap = query_tokens & text_tokens
        exact = query.strip().lower() in {tool.id.lower(), tool.label.lower()}
        score = (len(overlap) / max(len(query_tokens), 1)) + (1.0 if exact else 0.0)
        rows.append((tool, score))
    rows.sort(key=lambda row: (row[1], row[0].label.lower()), reverse=True)
    return rows[:top_k]


def resolve_workflow(
    tool_id: str,
    *,
    settings: Any | None = None,
    user_id: str | None = None,
) -> NeuroimagingWorkflow:
    """Resolve one catalog workflow by its exact stable id."""
    for tool in workflows(settings=settings, user_id=user_id):
        if tool.id == tool_id:
            return tool
    raise ValueError(f"Workflow {tool_id!r} was not found.")


def inspect_workflow(
    tool_id: str,
    *,
    settings: Any | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Return the lazy-loaded workflow contract exposed to the assistant."""
    tool = resolve_workflow(tool_id, settings=settings, user_id=user_id)
    return {
        "tool_id": tool.id,
        "image": tool.image,
        "description": tool.description,
        "details": tool.details,
        "inputs": [item.model_dump() for item in tool.inputs],
        "outputs": [item.model_dump() for item in tool.outputs],
        "execution": tool.execution.model_dump(),
        "return": tool.return_policy.model_dump(),
    }


def run_analysis_workflows_payload(
    *,
    settings: Any | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the effective per-user workflow catalog for the Run Analysis UI."""
    return [
        {
            "id": tool.id,
            "label": tool.label,
            "description": tool.description,
            "inputs": [item.model_dump() for item in tool.inputs],
            "outputs": [item.model_dump() for item in tool.outputs],
            "execution": tool.execution.model_dump(),
            "input_artifact_kind": "intensity_volume",
        }
        for tool in workflows(settings=settings, user_id=user_id)
    ]
