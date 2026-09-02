"""User-facing activity state for one managed assistant turn."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AssistantActivity(BaseModel):
    kind: Literal["model", "tool", "workflow", "image"]
    label: str
    blocking: bool = True
    run_id: str | None = None
    mode: Literal["synchronous", "background"] | None = None
    device: Literal["cpu", "gpu"] | None = None
    phase: Literal["waiting", "downloading", "extracting", "preparing", "verifying", "ready"] | None = None
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    completed_layers: int | None = Field(default=None, ge=0)
    total_layers: int | None = Field(default=None, ge=0)
    current_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    disk_free_bytes: int | None = Field(default=None, ge=0)
    disk_warning: str | None = None
    reclaimable_storage: dict[str, str] | None = None
    stalled_seconds: int | None = Field(default=None, ge=0)
    process_active: bool | None = None


def model_activity() -> AssistantActivity:
    return AssistantActivity(kind="model", label="Assistant")
