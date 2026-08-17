"""Provide API service schemas behavior for NeuroCade."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class UserSummary(BaseModel):
    id: str
    email: str
    full_name: str


class FrontendConfig(BaseModel):
    local_auth_enabled: bool
    clerk_publishable_key: str | None = None
    clerk_jwt_template: str | None = None


class SessionBootstrap(BaseModel):
    user: UserSummary
    features: dict[str, bool]
    workspaces: list[dict[str, Any]] = Field(default_factory=list)
    default_workspace_id: str | None = None


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    role: str
    kind: str
    is_default: bool
    case_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceCreateRequest(BaseModel):
    name: str
    description: str | None = None


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


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


class AnalysisToolSummary(BaseModel):
    id: str
    label: str
    description: str
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    execution: dict[str, Any] = Field(default_factory=dict)
    input_artifact_kind: Literal["intensity_volume"]


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


class CaseUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    modalities: list[str] | None = None
    tags: list[str] | None = None
    notes: str | None = None


class CaseUpdateResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class ProviderSummary(BaseModel):
    provider: str
    provider_family: str
    model: str
    is_default: bool = False
    vision: bool = False
    configured: bool
    reachable: bool
    configuration_reason: str | None = None
    reachability_reason: str | None = None


class ChatToolCallEntry(BaseModel):
    call_id: str | None = None
    execution_id: str | None = None
    ledger_status: str | None = None
    external_run_id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str
    is_error: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    terminal: bool = False
    elapsed_ms: int | None = None


class ReasoningEntry(BaseModel):
    summary: str
    round: int | None = None
    tool_names: list[str] = Field(default_factory=list)


class ChatMessageSummary(BaseModel):
    role: str
    content: Any
    toolCalls: list[ChatToolCallEntry] = Field(default_factory=list)
    reasoningEntries: list[ReasoningEntry] = Field(default_factory=list)


class AssistantTextContentPart(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=20_000)


class AssistantImageUrl(BaseModel):
    url: str = Field(min_length=1, max_length=5_000_000)


class AssistantImageContentPart(BaseModel):
    type: Literal["image_url"]
    image_url: AssistantImageUrl


class AssistantTurnMessage(BaseModel):
    role: Literal["user"]
    content: str | list[AssistantTextContentPart | AssistantImageContentPart]

    @model_validator(mode="after")
    def validate_content(self) -> "AssistantTurnMessage":
        if isinstance(self.content, str):
            if not self.content.strip():
                raise ValueError("Message content must not be blank")
            if len(self.content) > 20_000:
                raise ValueError("Message text exceeds the 20,000 character limit")
            return self
        if not self.content or len(self.content) > 4:
            raise ValueError("Structured message content must contain between 1 and 4 parts")
        return self


class AssistantToolApproval(BaseModel):
    """One user-approved, exact assistant tool invocation."""

    name: str = Field(min_length=1, max_length=255)
    call_id: str | None = Field(default=None, max_length=255)
    execution_id: str | None = Field(default=None, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class AssistantTurnRequest(BaseModel):
    messages: list[AssistantTurnMessage] = Field(min_length=1, max_length=1)
    workspace_id: str = Field(min_length=1, max_length=255)
    case_id: str | None = Field(default=None, max_length=255)
    gui_session_id: str = Field(min_length=1, max_length=255)
    scope: Literal["case", "workspace"] = "case"
    provider: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    gui_state_override: dict[str, Any] | None = None
    tool_approvals: list[AssistantToolApproval] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_gui_state_size(self) -> "AssistantTurnRequest":
        if self.gui_state_override is not None:
            import json

            if len(json.dumps(self.gui_state_override, default=str)) > 64_000:
                raise ValueError("GUI state override exceeds the 64,000 character limit")
        return self


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
    filenames: list[str]
    title: str


class StartRunRequest(BaseModel):
    tool_id: str
    case_id: str
    input_artifact_ids: list[str]
    output_name_overrides: dict[str, str] = Field(default_factory=dict)


class GuiLayerDisplay(BaseModel):
    brightness: float | None = None
    contrast: float | None = None
    surface_color_mode: Literal["solid", "curvature", "annotation"] | None = None


class GuiLayerState(BaseModel):
    id: str
    artifact_id: str | None = None
    filename: str
    name: str | None = None
    type: Literal["intensity", "segmentation", "surface"]
    role: str | None = None
    hemisphere: Literal["left", "right"] | None = None
    loaded: bool = True
    visible: bool
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    display: GuiLayerDisplay = Field(default_factory=GuiLayerDisplay)


class GuiCursorState(BaseModel):
    voxel: tuple[float, float, float]
    label_id: int
    label_name: str


class GuiStateSyncRequest(BaseModel):
    workspace_id: str
    case_id: str | None = None
    gui_session_id: str
    is_job_running: bool = False
    layers: list[GuiLayerState] = Field(default_factory=list)
    acknowledged_command_ids: list[str] = Field(default_factory=list)
    current_intensity_artifact_id: str | None = None
    current_intensity_volume: str | None = None
    current_cursor: GuiCursorState | None = None
