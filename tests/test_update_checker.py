"""Tests for release-version resolution in the update checker."""

from __future__ import annotations

from scripts import update_checker


def test_release_build_version_replaces_placeholder_override(monkeypatch):
    monkeypatch.setenv("NEUROCADE_VERSION", "0.0.0")
    monkeypatch.setenv("NEUROCADE_BUILD_VERSION", "2026.7.31-beta.1")

    assert update_checker.current_version({}) == "2026.7.31-beta.1"


def test_explicit_runtime_version_overrides_release_build(monkeypatch):
    monkeypatch.setenv("NEUROCADE_VERSION", "2026.8.1-custom")
    monkeypatch.setenv("NEUROCADE_BUILD_VERSION", "2026.7.31")

    assert update_checker.current_version({}) == "2026.8.1-custom"
