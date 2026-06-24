"""Provide API service schemas behavior for NeuroCade."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class UserSummary(BaseModel):
    id: str
    email: str
    full_name: str


class SessionBootstrap(BaseModel):
    user: UserSummary
    role: str
    auth_mode: str
    deployment_profile: str
    public_url: str
    features: dict[str, bool]
    limits: dict[str, int] = Field(default_factory=dict)
    sample_data: dict[str, Any] = Field(default_factory=dict)
    workspaces: list[dict[str, Any]] = Field(default_factory=list)
    default_workspace_id: str | None = None
    active_workspace_id: str | None = None


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    role: str
    kind: str
    is_default: bool
    status: str
    case_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceCreateRequest(BaseModel):
    name: str
    description: str | None = None


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class WorkspaceDeleteRequest(BaseModel):
    confirm_non_empty_delete: bool = False


class ArtifactSummary(BaseModel):
    id: str
    case_id: str | None = None
    workspace_id: str | None = None
    kind: str
    name: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    download_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunSummary(BaseModel):
    id: str
    case_id: str | None = None
    workspace_id: str | None = None
    status: str
    run_type: str
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None


class CaseSummary(BaseModel):
    id: str
    title: str
    description: str | None = None
    modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    role: str
    workspace_id: str
    thread_id: str | None = None
    latest_run_status: str | None = None
    artifact_count: int = 0
    created_at: datetime
    updated_at: datetime


class CaseDetail(BaseModel):
    id: str
    title: str
    description: str | None
    modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    role: str
    workspace_id: str
    thread_id: str | None
    artifacts: list[ArtifactSummary]
    runs: list[RunSummary]


class WorkspaceBatchCaseSummary(BaseModel):
    run_id: str
    case_id: str
    case_title: str
    status: str
    external_task_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceBatchRunSummary(BaseModel):
    run_id: str
    workspace_id: str
    status: str
    run_type: str
    execution_mode: Literal["workspace_wide", "per_case"] = "per_case"
    command: str
    report_name: str
    analysis_id: str | None = None
    selected_case_count: int = 0
    total_cases: int = 0
    queued_cases: int = 0
    running_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    canceled_cases: int = 0
    external_task_id: str | None = None
    artifact_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceBatchRunDetail(WorkspaceBatchRunSummary):
    cases: list[WorkspaceBatchCaseSummary] = Field(default_factory=list)
    artifacts: list[ArtifactSummary] = Field(default_factory=list)


class CaseRenameRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    modalities: list[str] | None = None
    tags: list[str] | None = None
    notes: str | None = None


class CaseRenameResponse(BaseModel):
    old_id: str
    new_id: str
    title: str
    case_id: str
    old_title: str
    new_title: str
    description: str | None = None
    modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class ProviderSummary(BaseModel):
    provider: str
    provider_family: str
    model: str
    role: str
    is_default: bool = False
    native_tool_calling: bool = False
    json_mode: bool = True
    vision: bool = False
    streaming: bool = True
    available: bool = True
    availability_reason: str | None = None


class ChatToolCallEntry(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str


class ReasoningEntry(BaseModel):
    summary: str
    round: int | None = None
    tool_names: list[str] = Field(default_factory=list)


class ChatMessageSummary(BaseModel):
    role: str
    content: Any
    toolCalls: list[ChatToolCallEntry] = Field(default_factory=list)
    reasoningEntries: list[ReasoningEntry] = Field(default_factory=list)


class AssistantTurnRequest(BaseModel):
    messages: list[dict[str, Any]]
    workspace_id: str | None = None
    case_id: str | None = None
    gui_session_id: str | None = None
    scope: str = "case"
    provider: str | None = None
    model: str | None = None
    gui_state_override: dict[str, Any] | None = None


class AssistantHistoryResponse(BaseModel):
    thread_id: str | None = None
    messages: list[ChatMessageSummary] = Field(default_factory=list)


class AssistantHistoryClearResponse(BaseModel):
    status: str


class MonitoringClientErrorRequest(BaseModel):
    level: str = "error"
    event_type: str = "frontend.error"
    message: str
    path: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MonitoringStatusItem(BaseModel):
    name: str
    status: Literal["ok", "degraded", "down", "unknown"]
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MonitoringUserSummary(BaseModel):
    id: str
    email: str
    full_name: str
    last_seen_at: datetime | None = None


class MonitoringEventSummary(BaseModel):
    id: str
    source: str
    level: str
    event_type: str
    message: str
    user_id: str | None = None
    user_email: str | None = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MonitoringAuditEventSummary(BaseModel):
    id: str
    action: str
    user_id: str | None = None
    user_email: str | None = None
    case_id: str | None = None
    artifact_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MonitoringSummary(BaseModel):
    generated_at: datetime
    status: Literal["ok", "degraded", "down"]
    active_window_minutes: int
    totals: dict[str, int]
    active_users: list[MonitoringUserSummary]
    services: list[MonitoringStatusItem]
    jobs: dict[str, Any]
    recent_errors: list[MonitoringEventSummary]
    recent_activity: list[MonitoringAuditEventSummary]


class MonitoringHealth(BaseModel):
    generated_at: datetime
    status: Literal["ok", "degraded", "down"]
    services: list[MonitoringStatusItem]
    jobs: dict[str, Any]


class MonitoringEventsResponse(BaseModel):
    events: list[MonitoringEventSummary]
    audit_events: list[MonitoringAuditEventSummary]


class MonitoringIngestResponse(BaseModel):
    status: str


class UploadResponse(BaseModel):
    case_id: str
    workspace_id: str
    filename: str
    title: str


class StartRunRequest(BaseModel):
    workspace_id: str | None = None
    case_id: str | None = None
    source_case_id: str | None = None
    input_artifact_id: str
    seg_only: bool = False
    surf_only: bool = False
    no_bias: bool = False
    no_cereb: bool = False
    no_asegdkt: bool = False
    no_hypothal: bool = False
    three_t: bool = False
    vox_size: str = "min"
    case_name: str | None = None


class GuiStateSyncRequest(BaseModel):
    workspace_id: str | None = None
    case_id: str | None = None
    gui_session_id: str | None = None
    is_job_running: bool = False
    has_valid_segmentation: bool = False
    current_case_id: str | None = None
    loaded_volumes: list[str] = Field(default_factory=list)
    loaded_volume_names: list[str] = Field(default_factory=list)
    visible_volumes: list[str] = Field(default_factory=list)
    current_intensity_artifact_id: str | None = None
    current_intensity_volume: str | None = None
    current_cursor: dict[str, Any] | None = None
