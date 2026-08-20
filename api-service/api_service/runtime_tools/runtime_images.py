"""Resolve catalog image strings to shared immutable runtime image specs."""

from __future__ import annotations

from functools import lru_cache

from neurocade_runtime_tools.images import load_image_manifest
from neurocade_runtime_tools.protocol import RuntimeImageSpec

from backend_common.settings import ROOT_DIR


@lru_cache(maxsize=1)
def _builtins() -> dict[str, RuntimeImageSpec]:
    specs = load_image_manifest(ROOT_DIR / "config" / "tool_images.json")
    return {spec.oci_reference: spec for spec in specs}


def runtime_image_spec(image: str) -> RuntimeImageSpec:
    """Return a release-pinned built-in spec or a tagged dynamic OCI spec."""
    return _builtins().get(image, RuntimeImageSpec(oci_reference=image))
