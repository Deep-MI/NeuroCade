"""Canonical structured approval contracts shared by runtime and API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

type AssistantApprovalAction = Literal[
    "config_upsert",
    "config_delete",
    "run_cancel",
    "file_write",
    "file_edit",
]
type AssistantApprovalTone = Literal["warning", "danger"]


class AssistantWorkflowApprovalItem(BaseModel):
    name: str
    description: str
    path: str


class AssistantWorkflowApprovalExecution(BaseModel):
    mode: Literal["background", "synchronous"]
    gpu: bool


class AssistantWorkflowApprovalPresentation(BaseModel):
    kind: Literal["workflow"]
    title: str
    description: str
    details: str
    inputs: list[AssistantWorkflowApprovalItem] = Field(default_factory=list)
    outputs: list[AssistantWorkflowApprovalItem] = Field(default_factory=list)
    execution: AssistantWorkflowApprovalExecution


class AssistantActionApprovalRow(BaseModel):
    label: str
    value: str
    code: bool


class AssistantActionApprovalSection(BaseModel):
    label: str
    rows: list[AssistantActionApprovalRow] = Field(default_factory=list)


class AssistantActionApprovalDetail(BaseModel):
    summary: str
    content: str
    language: str | None = None


class AssistantActionApprovalPresentation(BaseModel):
    kind: Literal["action"]
    action: AssistantApprovalAction
    title: str
    description: str
    confirm_label: str
    tone: AssistantApprovalTone
    sections: list[AssistantActionApprovalSection] = Field(default_factory=list)
    details: list[AssistantActionApprovalDetail] = Field(default_factory=list)


type AssistantApprovalPresentation = (
    AssistantWorkflowApprovalPresentation | AssistantActionApprovalPresentation
)
