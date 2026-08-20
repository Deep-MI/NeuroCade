"""Tests for application-side NeuroDesk discovery; preparation is bridge-owned."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant.tools import image_tools as image_tools_module
from api_service.assistant.tools.definition import ToolExecutionContext
from api_service.runtime_tools import neurodesk_images as images_module
from api_service.runtime_tools.neurodesk_images import (
    LoadedImageCatalog,
    NeurodeskImageCatalog,
    load_image_catalog,
    parse_catalog_manifest,
    search_images,
)

MANIFEST = """\
ants_2.9.0_20250101 categories:image registration,image segmentation,structural imaging
ants_2.10.0_20260101 categories:image registration,image segmentation,structural imaging
fsl_6.0.7_20250201 categories:diffusion imaging,functional imaging
"""


@pytest.fixture(autouse=True)
def reset_refresh_backoff():  # noqa: ANN201
    images_module._FAILED_REFRESH_RETRY_AT = 0.0
    yield
    images_module._FAILED_REFRESH_RETRY_AT = 0.0


def test_repository_fallback_is_complete_and_normalized() -> None:
    catalog = NeurodeskImageCatalog.model_validate_json(images_module.FALLBACK_PATH.read_text(encoding="utf-8"))
    assert len(catalog.images) >= 300
    assert len({image.neurodesk_id for image in catalog.images}) == len(catalog.images)
    ants = next(image for image in catalog.images if image.neurodesk_id == "ants_2.6.5_20260602")
    assert ants.image == "vnmd/ants_2.6.5:20260602"


def test_manifest_parser_rejects_malformed_and_duplicate_rows() -> None:
    with pytest.raises(ValueError, match="line 1"):
        parse_catalog_manifest("not-a-container")
    with pytest.raises(ValueError, match="Duplicate"):
        parse_catalog_manifest("ants_2.6.5_20260602 categories:registration\n" * 2)


def test_search_is_bounded_and_collapses_old_versions() -> None:
    catalog = parse_catalog_manifest(MANIFEST)
    matches, total = search_images(catalog, query="ants denoising", limit=1)
    assert total == 1
    assert matches[0].neurodesk_id == "ants_2.10.0_20260101"


def test_catalog_refresh_writes_cache_and_offline_load_uses_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    settings = SimpleNamespace(fs_data_root=tmp_path)
    catalog = parse_catalog_manifest(MANIFEST)
    monkeypatch.setattr(images_module, "fetch_image_catalog", lambda: catalog)
    assert load_image_catalog(settings=settings).source == "remote"
    old = catalog.model_copy(update={"generated_at": datetime.now(UTC) - timedelta(days=2)})
    images_module._atomic_write_catalog(images_module.catalog_cache_path(settings), old)
    monkeypatch.setattr(images_module, "fetch_image_catalog", lambda: (_ for _ in ()).throw(requests.ConnectionError("offline")))
    assert load_image_catalog(settings=settings).stale is True


def test_dynamic_image_resolution_validates_catalog_without_preparing(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = parse_catalog_manifest(MANIFEST)
    monkeypatch.setattr(images_module, "load_image_catalog", lambda **_kwargs: LoadedImageCatalog(catalog, "fallback", False))
    assert images_module.validate_catalog_image("vnmd/ants_2.10.0:20260101") == "vnmd/ants_2.10.0:20260101"
    with pytest.raises(ValueError, match="Unknown"):
        images_module.validate_catalog_image("vnmd/missing_1:20260101")


def test_assistant_image_search_returns_compact_page(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    settings = SimpleNamespace(fs_data_root=tmp_path)
    catalog = parse_catalog_manifest(MANIFEST)
    monkeypatch.setattr(image_tools_module, "load_image_catalog", lambda **_kwargs: LoadedImageCatalog(catalog, "fallback", True))
    tools = image_tools_module.AssistantImageTools(settings=settings)
    result = asyncio.run(tools.search({}, ToolExecutionContext("image-search"), {"query": "ants denoising"}))
    payload = json.loads(result.content)
    assert result.is_error is False
    assert payload["items"][0]["image"] == "vnmd/ants_2.10.0:20260101"
    assert result.details["catalog_stale"] is True
