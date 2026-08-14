"""Discover and cache version-pinned NeuroDesk container images."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from neurocade_runtime_tools.runtime_backends import (
    SIF_DIR_ENV,
    apptainer_sif_path,
    require_network_disabled_image,
    select_runtime_backend,
)
from pydantic import BaseModel, Field

from backend_common.settings import ROOT_DIR, get_settings

CATALOG_URL = "https://raw.githubusercontent.com/NeuroDesk/neurocommand/main/cvmfs/log.txt"
IMAGE_BASE_URL = "https://neurocontainers.neurodesk.workers.dev"
FALLBACK_PATH = ROOT_DIR / "config" / "neurodesk_images.json"
CACHE_RELATIVE_PATH = Path(".neurocade") / "neurodesk_images.json"
CATALOG_TTL_SECONDS = 24 * 60 * 60
FAILED_REFRESH_RETRY_SECONDS = 5 * 60
HTTP_TIMEOUT_SECONDS = 15
MAX_CATALOG_BYTES = 2 * 1024 * 1024
_CONTAINER_ID_PATTERN = re.compile(r"^(?P<family>.+)_(?P<version>[^_]+)_(?P<build_date>\d{8})$")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_CATALOG_LOCK = threading.Lock()
_IMAGE_LOCK = threading.Lock()
_FAILED_REFRESH_RETRY_AT = 0.0


class NeurodeskImage(BaseModel):
    """One normalized entry from NeuroDesk's public container manifest."""

    neurodesk_id: str
    family: str
    version: str
    build_date: str
    categories: list[str] = Field(default_factory=list)

    @property
    def image(self) -> str:
        return f"vnmd/{self.family}_{self.version}:{self.build_date}"

    @property
    def download_url(self) -> str:
        return f"{IMAGE_BASE_URL}/{self.neurodesk_id}.simg"


class NeurodeskImageCatalog(BaseModel):
    version: int = 1
    source_url: str = CATALOG_URL
    generated_at: datetime
    images: list[NeurodeskImage]


@dataclass(frozen=True, slots=True)
class LoadedImageCatalog:
    catalog: NeurodeskImageCatalog
    source: str
    stale: bool


def parse_catalog_manifest(text: str, *, generated_at: datetime | None = None) -> NeurodeskImageCatalog:
    """Parse NeuroDesk's compact line-oriented manifest into validated records."""
    if len(text.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("NeuroDesk image catalog exceeds the maximum allowed size")
    images: list[NeurodeskImage] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            container_id, raw_categories = line.split(" categories:", 1)
        except ValueError as exc:
            raise ValueError(f"Invalid NeuroDesk catalog line {line_number}") from exc
        match = _CONTAINER_ID_PATTERN.fullmatch(container_id)
        if match is None:
            raise ValueError(f"Invalid NeuroDesk container id on line {line_number}: {container_id!r}")
        if container_id in seen:
            raise ValueError(f"Duplicate NeuroDesk container id: {container_id}")
        seen.add(container_id)
        images.append(
            NeurodeskImage(
                neurodesk_id=container_id,
                family=match.group("family"),
                version=match.group("version"),
                build_date=match.group("build_date"),
                categories=[value.strip() for value in raw_categories.split(",") if value.strip()],
            )
        )
    if not images:
        raise ValueError("NeuroDesk image catalog is empty")
    return NeurodeskImageCatalog(generated_at=generated_at or datetime.now(UTC), images=images)


def _atomic_write_catalog(path: Path, catalog: NeurodeskImageCatalog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_catalog(path: Path) -> NeurodeskImageCatalog:
    return NeurodeskImageCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def fetch_image_catalog() -> NeurodeskImageCatalog:
    """Fetch and normalize the official manifest with a strict response-size bound."""
    response = requests.get(CATALOG_URL, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    content_length = int(response.headers.get("content-length") or 0)
    if content_length > MAX_CATALOG_BYTES:
        raise ValueError("NeuroDesk image catalog exceeds the maximum allowed size")
    return parse_catalog_manifest(response.text)


def catalog_cache_path(settings: Any | None = None) -> Path:
    return Path((settings or get_settings()).fs_data_root) / CACHE_RELATIVE_PATH


def _is_fresh(catalog: NeurodeskImageCatalog, now: datetime) -> bool:
    generated_at = catalog.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    return 0 <= (now - generated_at).total_seconds() <= CATALOG_TTL_SECONDS


def load_image_catalog(*, settings: Any | None = None) -> LoadedImageCatalog:
    """Load a fresh cache, refresh it automatically, or use the repository fallback."""
    global _FAILED_REFRESH_RETRY_AT
    cache_path = catalog_cache_path(settings)
    now = datetime.now(UTC)
    with _CATALOG_LOCK:
        cached: NeurodeskImageCatalog | None = None
        with suppress(OSError, ValueError):
            cached = _read_catalog(cache_path)
        if cached is not None and _is_fresh(cached, now):
            return LoadedImageCatalog(cached, "cache", False)
        if time.monotonic() < _FAILED_REFRESH_RETRY_AT:
            if cached is not None:
                return LoadedImageCatalog(cached, "cache", True)
            return LoadedImageCatalog(_read_catalog(FALLBACK_PATH), "fallback", True)
        try:
            fetched = fetch_image_catalog()
            _atomic_write_catalog(cache_path, fetched)
            _FAILED_REFRESH_RETRY_AT = 0.0
            return LoadedImageCatalog(fetched, "remote", False)
        except (OSError, ValueError, requests.RequestException):
            _FAILED_REFRESH_RETRY_AT = time.monotonic() + FAILED_REFRESH_RETRY_SECONDS
            if cached is not None:
                return LoadedImageCatalog(cached, "cache", True)
            return LoadedImageCatalog(_read_catalog(FALLBACK_PATH), "fallback", True)


def _natural_version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", version.lower().lstrip("vr"))
        if part
    )


def latest_images(images: list[NeurodeskImage]) -> list[NeurodeskImage]:
    """Return the highest natural version/build for each tool family."""
    latest: dict[str, NeurodeskImage] = {}
    for image in images:
        previous = latest.get(image.family)
        key = (_natural_version_key(image.version), image.build_date)
        if previous is None or key > (_natural_version_key(previous.version), previous.build_date):
            latest[image.family] = image
    return list(latest.values())


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(value.lower()))


def _search_score(image: NeurodeskImage, query: str) -> float:
    cleaned = query.strip().lower()
    if not cleaned:
        return 0.0
    searchable = " ".join((image.neurodesk_id, image.family, image.version, *image.categories)).lower()
    query_tokens = _tokens(cleaned)
    score = 10.0 if cleaned == image.family.lower() else 0.0
    score += 4.0 if cleaned in image.neurodesk_id.lower() else 0.0
    score += 2.0 if cleaned in searchable else 0.0
    return score + len(query_tokens & _tokens(searchable)) / max(len(query_tokens), 1)


def search_images(
    catalog: NeurodeskImageCatalog,
    *,
    query: str = "",
    latest_only: bool = True,
    offset: int = 0,
    limit: int = 8,
) -> tuple[list[NeurodeskImage], int]:
    """Search or browse the normalized catalog while keeping output bounded."""
    images = latest_images(catalog.images) if latest_only else list(catalog.images)
    rows: list[tuple[NeurodeskImage, float]] = []
    for image in images:
        score = _search_score(image, query)
        if query.strip() and score <= 0:
            continue
        rows.append((image, score))
    rows.sort(key=lambda row: (-row[1], row[0].family.lower(), _natural_version_key(row[0].version)))
    total = len(rows)
    return [image for image, _score in rows[offset : offset + limit]], total


def find_image_by_reference(catalog: NeurodeskImageCatalog, image: str) -> NeurodeskImage | None:
    return next((item for item in catalog.images if item.image == image), None)


def prepare_neurodesk_image(
    image: NeurodeskImage,
    *,
    sif_dir: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Download a ready-to-run NeuroDesk SIF directly into the persistent cache."""
    resolved_sif_dir = sif_dir or os.environ.get(SIF_DIR_ENV)
    if not resolved_sif_dir:
        raise RuntimeError(f"{SIF_DIR_ENV} must be configured")
    target = apptainer_sif_path(image.image, sif_dir=resolved_sif_dir)
    with _IMAGE_LOCK:
        if target.is_file() and target.stat().st_size > 0 and not force:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.stem}.partial.sif")
        temporary.unlink(missing_ok=True)
        try:
            with requests.get(image.download_url, stream=True, timeout=HTTP_TIMEOUT_SECONDS) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            if temporary.stat().st_size == 0:
                raise RuntimeError("Downloaded NeuroDesk image is empty")
            subprocess.run(
                ["apptainer", "inspect", str(temporary)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def ensure_image_prepared(
    image: str,
    *,
    settings: Any | None = None,
    force: bool = False,
) -> Path | None:
    """Prepare a cataloged NeuroDesk image on first use; ignore other registries."""
    if select_runtime_backend().name != "apptainer" or not image.startswith("vnmd/"):
        return None
    loaded = load_image_catalog(settings=settings)
    record = find_image_by_reference(loaded.catalog, image)
    if record is None:
        return None
    resolved_settings = settings or get_settings()
    sif_dir = Path(resolved_settings.sif_dir)
    return prepare_neurodesk_image(record, sif_dir=sif_dir, force=force)


def resolve_or_prepare_image(image: str, *, settings: Any | None = None) -> str:
    """Return a runnable image reference, downloading a known NeuroDesk SIF on cache miss."""
    try:
        return require_network_disabled_image(image)
    except RuntimeError:
        prepared = ensure_image_prepared(image, settings=settings)
        if prepared is None:
            raise
        return str(prepared)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-fallback",
        action="store_true",
        help="Fetch the official manifest and rewrite config/neurodesk_images.json.",
    )
    args = parser.parse_args()
    if not args.refresh_fallback:
        parser.error("choose --refresh-fallback")
    catalog = fetch_image_catalog()
    _atomic_write_catalog(FALLBACK_PATH, catalog)
    print(f"Wrote {len(catalog.images)} NeuroDesk images to {FALLBACK_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
