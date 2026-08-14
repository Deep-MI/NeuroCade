"""Tests for NeuroDesk image discovery and direct SIF caching."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.assistant.tools import image_tools as image_tools_module  # noqa: E402
from api_service.assistant.tools.definition import ToolExecutionContext  # noqa: E402
from api_service.runtime_tools import neurodesk_images as images_module  # noqa: E402
from api_service.runtime_tools.neurodesk_images import (  # noqa: E402
    LoadedImageCatalog,
    NeurodeskImageCatalog,
    load_image_catalog,
    parse_catalog_manifest,
    prepare_neurodesk_image,
    resolve_or_prepare_image,
    search_images,
)
from neurocade_runtime_tools.runtime_backends import apptainer_sif_path  # noqa: E402

MANIFEST = """\
ants_2.9.0_20250101 categories:image registration,image segmentation,structural imaging
ants_2.10.0_20260101 categories:image registration,image segmentation,structural imaging
fsl_6.0.7_20250201 categories:diffusion imaging,functional imaging
"""


@pytest.fixture(autouse=True)
def reset_refresh_backoff():
    images_module._FAILED_REFRESH_RETRY_AT = 0.0
    yield
    images_module._FAILED_REFRESH_RETRY_AT = 0.0


def test_repository_fallback_is_complete_and_normalized():
    catalog = NeurodeskImageCatalog.model_validate_json(
        images_module.FALLBACK_PATH.read_text(encoding="utf-8")
    )

    assert len(catalog.images) >= 300
    assert len({image.neurodesk_id for image in catalog.images}) == len(catalog.images)
    ants = next(image for image in catalog.images if image.neurodesk_id == "ants_2.6.5_20260602")
    assert ants.image == "vnmd/ants_2.6.5:20260602"
    assert ants.download_url == (
        "https://neurocontainers.neurodesk.workers.dev/ants_2.6.5_20260602.simg"
    )


def test_manifest_parser_rejects_malformed_and_duplicate_rows():
    with pytest.raises(ValueError, match="line 1"):
        parse_catalog_manifest("not-a-container")
    duplicate = "ants_2.6.5_20260602 categories:image registration\n" * 2
    with pytest.raises(ValueError, match="Duplicate"):
        parse_catalog_manifest(duplicate)


def test_search_is_bounded_browsable_and_collapses_old_versions():
    catalog = parse_catalog_manifest(MANIFEST)
    matches, total = search_images(catalog, query="ants denoising", limit=1)

    assert total == 1
    assert len(matches) == 1
    assert matches[0].neurodesk_id == "ants_2.10.0_20260101"

    browse, browse_total = search_images(
        catalog,
        query="diffusion functional",
        latest_only=False,
        limit=20,
    )
    assert browse_total == 1
    assert browse[0].family == "fsl"


def test_catalog_refresh_writes_cache_and_offline_load_uses_fallback(monkeypatch, tmp_path):
    settings = SimpleNamespace(fs_data_root=tmp_path)
    catalog = parse_catalog_manifest(MANIFEST)
    monkeypatch.setattr(images_module, "fetch_image_catalog", lambda: catalog)

    loaded = load_image_catalog(settings=settings)

    assert loaded.source == "remote"
    assert loaded.stale is False
    assert images_module.catalog_cache_path(settings).is_file()

    old = catalog.model_copy(update={"generated_at": datetime.now(UTC) - timedelta(days=2)})
    images_module._atomic_write_catalog(images_module.catalog_cache_path(settings), old)
    refresh_attempts = 0

    def fail_refresh():
        nonlocal refresh_attempts
        refresh_attempts += 1
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(
        images_module,
        "fetch_image_catalog",
        fail_refresh,
    )
    stale = load_image_catalog(settings=settings)
    assert stale.source == "cache"
    assert stale.stale is True

    images_module.catalog_cache_path(settings).unlink()
    monkeypatch.setattr(images_module, "FALLBACK_PATH", ROOT / "config" / "neurodesk_images.json")
    fallback = load_image_catalog(settings=settings)
    assert fallback.source == "fallback"
    assert len(fallback.catalog.images) >= 300
    assert refresh_attempts == 1


class _FakeDownload:
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, *, chunk_size):
        assert chunk_size == 1024 * 1024
        yield b"direct-neurodesk-sif"


def test_prepare_downloads_direct_simg_atomically_without_conversion(monkeypatch, tmp_path):
    image = parse_catalog_manifest(MANIFEST).images[0]
    requested: list[tuple[str, bool]] = []
    inspected: list[list[str]] = []

    def fake_get(url, *, stream, timeout):
        requested.append((url, stream))
        assert timeout == images_module.HTTP_TIMEOUT_SECONDS
        return _FakeDownload()

    monkeypatch.setattr(images_module.requests, "get", fake_get)
    monkeypatch.setattr(
        images_module.subprocess,
        "run",
        lambda argv, **_kwargs: inspected.append(argv) or SimpleNamespace(returncode=0),
    )

    path = prepare_neurodesk_image(image, sif_dir=tmp_path)

    assert path == apptainer_sif_path(image.image, sif_dir=tmp_path)
    assert path.read_bytes() == b"direct-neurodesk-sif"
    assert requested == [(image.download_url, True)]
    assert inspected[0][:2] == ["apptainer", "inspect"]
    assert not list(tmp_path.glob("*.partial.sif"))

    cached_path = prepare_neurodesk_image(image, sif_dir=tmp_path)
    assert cached_path == path
    assert len(requested) == 1


def test_prepare_serializes_concurrent_downloads(monkeypatch, tmp_path):
    image = parse_catalog_manifest(MANIFEST).images[0]
    requested: list[str] = []

    def fake_get(url, **_kwargs):
        requested.append(url)
        return _FakeDownload()

    monkeypatch.setattr(images_module.requests, "get", fake_get)
    monkeypatch.setattr(
        images_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        paths = list(executor.map(lambda _index: prepare_neurodesk_image(image, sif_dir=tmp_path), range(2)))

    assert paths[0] == paths[1]
    assert paths[0].read_bytes() == b"direct-neurodesk-sif"
    assert requested == [image.download_url]


def test_prepare_removes_partial_image_when_validation_fails(monkeypatch, tmp_path):
    image = parse_catalog_manifest(MANIFEST).images[0]
    monkeypatch.setattr(images_module.requests, "get", lambda *_args, **_kwargs: _FakeDownload())

    def fail_inspect(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "apptainer inspect")

    monkeypatch.setattr(images_module.subprocess, "run", fail_inspect)

    with pytest.raises(subprocess.CalledProcessError):
        prepare_neurodesk_image(image, sif_dir=tmp_path)

    assert not apptainer_sif_path(image.image, sif_dir=tmp_path).exists()
    assert not list(tmp_path.glob("*.partial.sif"))


def test_resolve_or_prepare_downloads_only_after_a_cache_miss(monkeypatch, tmp_path):
    prepared = tmp_path / "ants.sif"
    calls: list[str] = []
    monkeypatch.setattr(
        images_module,
        "require_network_disabled_image",
        lambda image: (_ for _ in ()).throw(RuntimeError(f"missing {image}")),
    )
    monkeypatch.setattr(
        images_module,
        "ensure_image_prepared",
        lambda image, **_kwargs: calls.append(image) or prepared,
    )

    assert resolve_or_prepare_image("vnmd/ants_2.6.5:20260602") == str(prepared)
    assert calls == ["vnmd/ants_2.6.5:20260602"]


def test_assistant_image_search_returns_compact_page(monkeypatch, tmp_path):
    settings = SimpleNamespace(fs_data_root=tmp_path)
    catalog = parse_catalog_manifest(MANIFEST)
    monkeypatch.setattr(
        image_tools_module,
        "load_image_catalog",
        lambda **_kwargs: LoadedImageCatalog(catalog, "fallback", True),
    )
    tools = image_tools_module.AssistantImageTools(settings=settings)

    result = asyncio.run(
        tools.search({}, ToolExecutionContext("image-search"), {"query": "ants denoising"})
    )
    payload = json.loads(result.content)

    assert result.is_error is False
    assert set(payload) == {"items", "total", "next_offset"}
    assert payload["items"] == [
        {
            "image": "vnmd/ants_2.10.0:20260101",
            "categories": ["image registration", "image segmentation", "structural imaging"],
        }
    ]
    assert result.details["catalog_source"] == "fallback"
    assert result.details["catalog_stale"] is True
    assert "download_url" not in result.content
