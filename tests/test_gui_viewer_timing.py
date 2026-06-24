"""Reproducible viewer timing analysis for NeuroCade.

Run with:
    scripts/analyze_viewer_timing.sh

This test is intentionally separate from CI-style assertions. It fails for
functional breakage, page errors, or API 500s, and records interaction timings
as analysis data instead of enforcing performance budgets.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from gui_helpers import DEFAULT_STORAGE_STATE_PATH, GATEWAY_URL, load_processed_case

pytest_plugins = ["conftest_gui"]


TARGETS_MS = {
    "tool": 100,
    "view": 250,
    "toggle_loaded_layer": 150,
    "layer_reorder": 150,
    "windowing": 150,
    "first_context_tab": 500,
    "cases_back": 750,
}


def _output_dir() -> Path:
    return Path(os.environ.get("NEUROCADE_TIMING_OUTPUT_DIR", "tests/screenshots/viewer-timing"))


def _debug_state(page) -> dict[str, Any]:
    return page.evaluate("""() => window.__neurocadeViewerDebug?.getState?.() ?? null""")


def _wait_for_viewer(page) -> None:
    page.wait_for_function(
        """() => {
          const state = window.__neurocadeViewerDebug?.getState?.();
          const spinner = document.querySelector('.nc-viewer-canvas-spinner');
          return state && state.mountedPaneCount >= 4 && state.loadedLayerIds.length > 0 && !spinner;
        }""",
        timeout=60_000,
    )
    page.wait_for_load_state("networkidle", timeout=15_000)
    settle_ms = int(os.environ.get("NEUROCADE_TIMING_SETTLE_MS", "3000"))
    if settle_ms > 0:
        page.wait_for_timeout(settle_ms)
        page.wait_for_load_state("networkidle", timeout=15_000)


def _measure(timings: list[dict[str, Any]], name: str, target_ms: int, action: Callable[[], None], wait: Callable[[], None] | None = None) -> None:
    start = time.perf_counter()
    action()
    if wait is not None:
        wait()
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    timings.append({
        "name": name,
        "duration_ms": duration_ms,
        "target_ms": target_ms,
        "over_target": duration_ms > target_ms,
    })


def _clear_case_persistence(page) -> None:
    page.add_init_script(
        """() => {
          for (const key of Object.keys(localStorage)) {
            if (key.startsWith('fastsurfer-case-')) localStorage.removeItem(key);
          }
        }"""
    )


def _load_timing_case(page) -> None:
    explicit_case_id = os.environ.get("NEUROCADE_TIMING_CASE_ID", "").strip()
    if explicit_case_id:
        if "__" not in explicit_case_id:
            raise AssertionError("NEUROCADE_TIMING_CASE_ID must use the canonical '<workspace_id>__<case_slug>' form")
        workspace_id, case_slug = explicit_case_id.split("__", 1)
        page.goto(f"{GATEWAY_URL}/workspaces/{workspace_id}/cases/{case_slug}", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(f"**/workspaces/{workspace_id}/cases/{case_slug}", timeout=15_000)
        return
    load_processed_case(page)


def test_viewer_interaction_timing_report(browser, services_up):
    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    context_kwargs: dict[str, Any] = {
        "viewport": {"width": 1440, "height": 900},
        "ignore_https_errors": True,
    }
    if DEFAULT_STORAGE_STATE_PATH.exists():
        context_kwargs["storage_state"] = str(DEFAULT_STORAGE_STATE_PATH)
    context = browser.new_context(**context_kwargs)
    page = context.new_page()

    console_messages: list[dict[str, str]] = []
    network_events: list[dict[str, Any]] = []
    page_errors: list[str] = []
    request_started: dict[int, float] = {}

    page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("request", lambda request: request_started.__setitem__(id(request), time.perf_counter()))

    def on_response(response) -> None:
        started = request_started.pop(id(response.request), None)
        duration_ms = None if started is None else round((time.perf_counter() - started) * 1000, 2)
        network_events.append({
            "url": response.url,
            "status": response.status,
            "duration_ms": duration_ms,
        })

    def on_request_failed(request) -> None:
        started = request_started.pop(id(request), None)
        network_events.append({
            "url": request.url,
            "failure": request.failure,
            "duration_ms": None if started is None else round((time.perf_counter() - started) * 1000, 2),
        })

    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)

    timings: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    interaction_failures: list[dict[str, str]] = []

    try:
        _clear_case_persistence(page)
        _load_timing_case(page)
        _wait_for_viewer(page)

        initial_state = _debug_state(page)
        assert initial_state, "Viewer debug state was unavailable"

        # Tool switching: pan, contrast/windowing, measurement.
        for mode in ("pan", "contrast", "measurement"):
            _measure(
                timings,
                f"tool:{mode}",
                TARGETS_MS["tool"],
                lambda mode=mode: page.locator(f"[data-testid='viewer-tool-{mode}']").click(),
                lambda mode=mode: page.wait_for_function(
                    """mode => window.__neurocadeViewerDebug?.getState?.().activeDragMode === mode""",
                    arg=mode,
                    timeout=5_000,
                ),
            )

        # View switching: axial, sagittal, coronal, grid, 3D.
        for view_mode in ("axial", "sagittal", "coronal", "multi", "render"):
            _measure(
                timings,
                f"view:{view_mode}",
                TARGETS_MS["view"],
                lambda view_mode=view_mode: page.locator(f"[data-testid='viewer-view-{view_mode}']").click(),
                lambda view_mode=view_mode: page.wait_for_function(
                    """viewMode => window.__neurocadeViewerDebug?.getState?.().activeViewMode === viewMode""",
                    arg=view_mode,
                    timeout=5_000,
                ),
            )

        # Show/hide an already-loaded image/segmentation layer.
        loaded_visible_ids = set(initial_state["loadedLayerIds"]) & set(initial_state["visibleLayerIds"])
        layer_items = page.locator("[data-testid='viewer-layer-item']")
        assert layer_items.count() > 0, "No viewer layers were available"
        toggle_index = 0
        for index in range(layer_items.count()):
            layer_id = layer_items.nth(index).get_attribute("data-layer-id")
            layer_type = layer_items.nth(index).get_attribute("data-layer-type")
            if layer_id in loaded_visible_ids and layer_type in {"intensity", "segmentation"}:
                toggle_index = index
                break
        toggle_item = layer_items.nth(toggle_index)
        toggle_id = toggle_item.get_attribute("data-layer-id")
        assert toggle_id, "Selected toggle layer did not expose a layer id"
        was_visible = toggle_id in _debug_state(page)["visibleLayerIds"]

        _measure(
            timings,
            "toggle:image-or-segmentation",
            TARGETS_MS["toggle_loaded_layer"],
            lambda: toggle_item.locator("[data-testid='viewer-layer-visibility']").click(),
            lambda: page.wait_for_function(
                """args => window.__neurocadeViewerDebug?.getState?.().visibleLayerIds.includes(args.id) === args.visible""",
                arg={"id": toggle_id, "visible": not was_visible},
                timeout=5_000,
            ),
        )
        _measure(
            timings,
            "toggle:image-or-segmentation:restore",
            TARGETS_MS["toggle_loaded_layer"],
            lambda: toggle_item.locator("[data-testid='viewer-layer-visibility']").click(),
            lambda: page.wait_for_function(
                """args => window.__neurocadeViewerDebug?.getState?.().visibleLayerIds.includes(args.id) === args.visible""",
                arg={"id": toggle_id, "visible": was_visible},
                timeout=5_000,
            ),
        )

        # Show/hide a surface if the deterministic case has one.
        surface_items = page.locator("[data-testid='viewer-layer-item'][data-layer-type='surface']")
        surface_index = None
        for index in range(surface_items.count()):
            candidate_id = surface_items.nth(index).get_attribute("data-layer-id")
            if candidate_id in loaded_visible_ids:
                surface_index = index
                break
        if surface_index is not None:
            surface_item = surface_items.nth(surface_index)
            surface_id = surface_item.get_attribute("data-layer-id")
            assert surface_id
            surface_was_visible = surface_id in _debug_state(page)["visibleLayerIds"]
            try:
                _measure(
                    timings,
                    "toggle:surface",
                    TARGETS_MS["toggle_loaded_layer"],
                    lambda: surface_item.locator("[data-testid='viewer-layer-visibility']").click(),
                    lambda: page.wait_for_function(
                        """args => window.__neurocadeViewerDebug?.getState?.().visibleLayerIds.includes(args.id) === args.visible""",
                        arg={"id": surface_id, "visible": not surface_was_visible},
                        timeout=5_000,
                    ),
                )
                _measure(
                    timings,
                    "toggle:surface:restore",
                    TARGETS_MS["toggle_loaded_layer"],
                    lambda: surface_item.locator("[data-testid='viewer-layer-visibility']").click(),
                    lambda: page.wait_for_function(
                        """args => window.__neurocadeViewerDebug?.getState?.().visibleLayerIds.includes(args.id) === args.visible""",
                        arg={"id": surface_id, "visible": surface_was_visible},
                        timeout=5_000,
                    ),
                )
            except Exception as error:  # noqa: BLE001 - report diagnostic and continue timing the remaining interactions.
                interaction_failures.append({"name": "toggle:surface", "error": str(error)})
        else:
            skipped.append({"name": "toggle:surface", "reason": "No already-loaded visible surface layer in timing case"})

        # Contrast/windowing: expand the first intensity layer, move min slider,
        # and assert the actual Niivue windowing readback changed.
        intensity_items = page.locator("[data-testid='viewer-layer-item'][data-layer-type='intensity']")
        assert intensity_items.count() > 0, "No intensity layer was available for windowing"
        intensity_item = intensity_items.first
        intensity_id = intensity_item.get_attribute("data-layer-id")
        assert intensity_id
        intensity_item.locator(".nc-layer-drag-handle").click()
        page.locator("[data-testid='viewer-window-min']").first.wait_for(state="visible", timeout=10_000)
        before_window = _debug_state(page)["windowings"].get(intensity_id)
        assert before_window, "Windowing readback was unavailable"
        original_min = before_window["calMin"]

        try:
            _measure(
                timings,
                "contrast:window-min",
                TARGETS_MS["windowing"],
                lambda: (
                    page.locator("[data-testid='viewer-window-min']").first.focus(),
                    page.keyboard.press("ArrowRight"),
                ),
                lambda: page.wait_for_function(
                    """args => {
                      const win = window.__neurocadeViewerDebug?.getState?.().windowings[args.id];
                      return win && Math.abs(win.calMin - args.originalMin) > 0.001;
                    }""",
                    arg={"id": intensity_id, "originalMin": original_min},
                    timeout=5_000,
                ),
            )
        except Exception as error:  # noqa: BLE001 - report diagnostic and continue timing the remaining interactions.
            interaction_failures.append({"name": "contrast:window-min", "error": str(error)})

        # Layer order: use keyboard reorder in the first section with at least 2 layers.
        reordered = False
        for layer_type in ("surface", "segmentation", "intensity"):
            section_items = page.locator(f"[data-testid='viewer-layer-item'][data-layer-type='{layer_type}']")
            if section_items.count() < 2:
                continue
            source_id = section_items.first.get_attribute("data-layer-id")
            before_order = _debug_state(page)["layerOrder"]
            section_items.first.locator(".nc-layer-drag-handle").focus()
            _measure(
                timings,
                f"layer-order:{layer_type}",
                TARGETS_MS["layer_reorder"],
                lambda: page.keyboard.press("ArrowDown"),
                lambda source_id=source_id, before_order=before_order: page.wait_for_function(
                    """args => {
                      const order = window.__neurocadeViewerDebug?.getState?.().layerOrder ?? [];
                      return order.indexOf(args.sourceId) > args.beforeOrder.indexOf(args.sourceId);
                    }""",
                    arg={"sourceId": source_id, "beforeOrder": before_order},
                    timeout=5_000,
                ),
            )
            reordered = True
            break
        if not reordered:
            skipped.append({"name": "layer-order", "reason": "No layer section had two layers"})

        # First context/tab switch: terminal panel is the normally hidden heavy
        # context panel when the case opens with chat active.
        _measure(
            timings,
            "context:terminal-first-open",
            TARGETS_MS["first_context_tab"],
            lambda: page.locator("button:has-text('Terminal')").click(),
            lambda: page.locator("[data-testid='terminal-content']").wait_for(state="visible", timeout=10_000),
        )

        # Cases back navigation stays part of this timing report because the
        # original slowdown was observed returning from a case to cases.
        _measure(
            timings,
            "navigation:back-to-cases",
            TARGETS_MS["cases_back"],
            lambda: page.locator("[data-testid='case-workspace-back']").click(),
            lambda: page.wait_for_function(
                """() => {
                  const hasFilter = !!document.querySelector("input[placeholder='Filter cases...']");
                  const loading = Array.from(document.querySelectorAll('span,div')).some((el) => el.textContent?.includes('Loading cases...'));
                  return hasFilter && !loading;
                }""",
                timeout=15_000,
            ),
        )
    finally:
        api_5xx = [event for event in network_events if event.get("status", 0) >= 500 and "/api/" in event.get("url", "")]
        console_errors = [
            message for message in console_messages
            if message["type"] == "error" and "WebGL" not in message["text"]
        ]
        slow_api = [
            event for event in network_events
            if event.get("duration_ms") is not None
            and event["duration_ms"] > 1000
            and "/api/" in event.get("url", "")
        ]
        report = {
            "gateway_url": GATEWAY_URL,
            "targets_ms": TARGETS_MS,
            "timings": timings,
            "skipped": skipped,
            "interaction_failures": interaction_failures,
            "slow_api_over_1000ms": slow_api,
            "api_5xx": api_5xx,
            "page_errors": page_errors,
            "console_errors": console_errors,
        }
        (output_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (output_dir / "console.json").write_text(json.dumps(console_messages, indent=2), encoding="utf-8")
        (output_dir / "network.json").write_text(json.dumps(network_events, indent=2), encoding="utf-8")
        context.close()

    assert not page_errors, f"Page errors during timing run: {page_errors}"
    assert not api_5xx, f"API 5xx responses during timing run: {api_5xx}"
    assert not console_errors, f"Console errors during timing run: {console_errors[:5]}"
