"""Bounded offline documentation lookup with explicit version provenance."""

import hashlib
import json
import re
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from api_service.assistant.tools.definition import ToolDefinition, ToolResult
from backend_common.settings import ROOT_DIR


@lru_cache(maxsize=1)
def bundle():
    path = ROOT_DIR / "config/documentation/bundle.json"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("Documentation manifest checksum mismatch")
    data = json.loads(raw)
    for page in data["pages"]:
        if hashlib.sha256(page["text"].encode()).hexdigest() != page["digest"]:
            raise ValueError("Documentation bundle checksum mismatch")
    return data


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str = Field(pattern="^(fastsurfer|neurocade)$")
    query: str = Field(min_length=1, max_length=1000)
    version: str | None = None
    tool_id: str | None = None
    run_id: str | None = None
    limit: int = Field(5, ge=1, le=10)


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(max_length=500)
    offset: int = Field(0, ge=0)
    max_characters: int = Field(12000, ge=1, le=20000)


def documentation_tools(state):
    async def search(_context, args):
        parsed = Search.model_validate(args)
        data = bundle()
        version = parsed.version
        inferred = False
        if parsed.run_id and parsed.tool_id:
            return ToolResult.error("Choose run_id or tool_id, not both")
        image = None
        if parsed.run_id:
            from backend_common.db import Run

            run = state["db"].get(Run, parsed.run_id)
            if run is None or run.workspace_id != state["workspace_id"] or (state.get("case_id") and run.case_id != state["case_id"]):
                return ToolResult.error("Run not found")
            image = (run.input_json or {}).get("workflow_definition", {}).get("image")
        elif parsed.tool_id:
            from api_service.runtime_tools.workflow_catalog import resolve_workflow
            from backend_common.settings import get_settings

            workflow = resolve_workflow(parsed.tool_id, settings=get_settings(), user_id=state["context"].user.id)
            image = workflow.neurodesk_image
        if parsed.run_id or parsed.tool_id:
            inferred_version = data["image_versions"].get(image)
            if inferred_version is None or parsed.product != "fastsurfer" or (version and version != inferred_version):
                return ToolResult.error("DOC_VERSION_UNAVAILABLE: no exact documentation mapping for this workflow/run")
            version = inferred_version
            inferred = True
        versions = data["versions"][parsed.product]
        defaulted = version is None
        version = version or versions[0]
        if version not in versions:
            return ToolResult.error("DOC_VERSION_UNAVAILABLE", details={"available_versions": versions})
        words = re.findall(r"[\w-]+", parsed.query.lower())
        scored = []
        for page in data["pages"]:
            if page["product"] != parsed.product or page["version"] != version:
                continue
            body = page["text"].lower()
            score = sum(min(body.count(word), 20) + 5 * page["heading"].lower().count(word) for word in words)
            if score:
                first = min((body.find(word) for word in words if word in body), default=0)
                hit = {k: v for k, v in page.items() if k != "text"}
                hit["excerpt"] = page["text"][max(0, first - 150) : max(0, first - 150) + 1500]
                scored.append((score, hit))
        scored.sort(key=lambda value: value[0], reverse=True)
        return ToolResult.structured(
            {
                "version": version,
                "default_version_used": defaulted,
                "matched_workflow": inferred,
                "matches": [hit for _, hit in scored[: parsed.limit]],
            }
        )

    async def read(_context, args):
        parsed = Read.model_validate(args)
        page = next((p for p in bundle()["pages"] if p["page_id"] == parsed.page_id), None)
        if page is None:
            return ToolResult.error("Documentation page not found")
        end = min(len(page["text"]), parsed.offset + parsed.max_characters)
        return ToolResult.structured(
            {**page, "text": page["text"][parsed.offset : end], "next_offset": end if end < len(page["text"]) else None}
        )

    return [
        ToolDefinition(
            "docs_search",
            "Search bundled FastSurfer or NeuroCade documentation. Select an exact version or tool_id; defaults are labeled. Documentation does not expand installed workflow capabilities.",
            Search.model_json_schema(),
            search,
            parallel_safe=True,
        ),
        ToolDefinition(
            "docs_read",
            "Read a documentation section returned by docs_search, with citations and bounded paging.",
            Read.model_json_schema(),
            read,
            parallel_safe=True,
        ),
    ]
