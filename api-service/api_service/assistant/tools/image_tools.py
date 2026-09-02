"""Assistant discovery for version-pinned NeuroDesk images."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

from api_service.assistant.tools.definition import ToolDefinition, ToolExecutionContext, ToolResult
from api_service.assistant.tools.registration import ToolRegistration
from api_service.runtime_tools.neurodesk_images import load_image_catalog, search_images


class ToolImageSearchArgs(BaseModel):
    query: str = Field(
        "",
        description="NeuroDesk application/package name, version, or imaging task. Leave empty to browse.",
    )
    latest_only: bool = Field(True, description="Return only the newest version of each tool family.")
    offset: int = Field(0, ge=0, description="Result offset for paging.")
    limit: int = Field(8, ge=1, le=20, description="Maximum results to return.")

    model_config = {"extra": "forbid"}


class AssistantImageTools:
    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    def build_tools(self, state: dict[str, Any]) -> list[ToolDefinition]:
        registration = ToolRegistration(
            "tool_image_search",
            "Find pinned NeuroDesk images. Leave query empty to browse; images download automatically on first use.",
            ToolImageSearchArgs.model_json_schema(),
            self.search,
            parallel_safe=True,
        )
        return [registration.bind(state)]

    async def search(
        self, _state: dict[str, Any], _execution: ToolExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            parsed = ToolImageSearchArgs.model_validate(arguments)
            loaded = await asyncio.to_thread(load_image_catalog, settings=self.settings)
            matches, total = search_images(
                loaded.catalog,
                query=parsed.query,
                latest_only=parsed.latest_only,
                offset=parsed.offset,
                limit=parsed.limit,
            )
            items = [
                {
                    "image": image.image,
                    "categories": image.categories,
                }
                for image in matches
            ]
            next_offset = parsed.offset + len(items) if parsed.offset + len(items) < total else None
            payload: dict[str, Any] = {
                "items": items,
                "total": total,
                "next_offset": next_offset,
            }
            details = {
                **payload,
                "catalog_source": loaded.source,
                "catalog_stale": loaded.stale,
            }
            return ToolResult.success(json.dumps(payload, separators=(",", ":")), details=details)
        except Exception as exc:
            return ToolResult.error(f"Error searching NeuroDesk images: {exc}")
