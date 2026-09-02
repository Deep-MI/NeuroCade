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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import DEMO_RUN_UPLOAD_FILENAME, build_fresh_uploaded_case, delete_workspace_via_api
from gui_helpers import APP_URL, DEFAULT_STORAGE_STATE_PATH

pytest_plugins = ["conftest_gui"]


TARGETS_MS = {
    "tool": 100,
    "view": 250,
    "toggle_loaded_layer": 150,
    "layer_reorder": 150,
    "opacity": 150,
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
          return state && state.viewerReady && state.loadedLayerIds.length > 0 && !spinner;
        }""",
        timeout=60_000,
    )
    page.wait_for_load_state("networkidle", timeout=15_000)
    settle_ms = int(os.environ.get("NEUROCADE_TIMING_SETTLE_MS", "15000"))
    if settle_ms > 0:
        page.wait_for_timeout(settle_ms)
        page.wait_for_load_state("networkidle", timeout=15_000)


def _measure(timings: list[dict[str, Any]], name: str, target_ms: int, action: Callable[[], None], wait: Callable[[], None] | None = None) -> None:
    start = time.perf_counter()
    action()
    action_done = time.perf_counter()
    if wait is not None:
        wait()
    end = time.perf_counter()
    timings.append({
        "name": name,
        "duration_ms": round((end - start) * 1000, 2),
        "action_ms": round((action_done - start) * 1000, 2),
        "wait_ms": round((end - action_done) * 1000, 2),
        "target_ms": target_ms,
        "over_target": (end - start) * 1000 > target_ms,
    })


def _measure_control_click(
    page,
    timings: list[dict[str, Any]],
    name: str,
    target_ms: int,
    locator,
    wait: Callable[[], None] | None = None,
) -> None:
    locator.wait_for(state="visible", timeout=5_000)
    locator.scroll_into_view_if_needed(timeout=5_000)
    _measure(timings, name, target_ms, lambda: locator.click(force=True, no_wait_after=True, timeout=5_000), wait)


def _clear_case_persistence(page) -> None:
    page.add_init_script(
        """() => {
          for (const key of Object.keys(localStorage)) {
            if (key.startsWith('neurocade-case-')) localStorage.removeItem(key);
          }
        }"""
    )


def _load_timing_case(page) -> str | None:
    explicit_case_id = os.environ.get("NEUROCADE_TIMING_CASE_ID", "").strip()
    if explicit_case_id:
        workspace_id = os.environ.get("NEUROCADE_TIMING_WORKSPACE_ID", "").strip()
        if not workspace_id:
            raise AssertionError("NEUROCADE_TIMING_WORKSPACE_ID is required with NEUROCADE_TIMING_CASE_ID")
        page.goto(f"{APP_URL}/workspaces/{workspace_id}/cases/{explicit_case_id}", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_url(f"**/workspaces/{workspace_id}/cases/{explicit_case_id}", timeout=15_000)
        return None
    if not DEMO_RUN_UPLOAD_FILENAME:
        pytest.skip(
            "Viewer timing requires NEUROCADE_TIMING_WORKSPACE_ID/NEUROCADE_TIMING_CASE_ID "
            "or an MRI upload fixture"
        )
    timing_case = build_fresh_uploaded_case(
        upload_filename=DEMO_RUN_UPLOAD_FILENAME,
        app_url=APP_URL,
        workspace_prefix="pytest-timing-workspace",
        case_prefix="pytest-timing-case",
    )
    page.goto(
        f"{APP_URL}/workspaces/{timing_case['workspace_id']}/cases/{timing_case['case_id']}",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    page.wait_for_url(
        f"**/workspaces/{timing_case['workspace_id']}/cases/{timing_case['case_id']}",
        timeout=15_000,
    )
    return timing_case["workspace_id"]


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
    settled_network_cutoff = 0
    disposable_workspace_id: str | None = None

    try:
        _clear_case_persistence(page)
        disposable_workspace_id = _load_timing_case(page)
        _wait_for_viewer(page)
        settled_network_cutoff = len(network_events)

        initial_state = _debug_state(page)
        assert initial_state, "Viewer debug state was unavailable"

        # Tool switching: pan, contrast/windowing, measurement.
        for mode in ("pan", "contrast", "measurement"):
            _measure_control_click(
                page,
                timings,
                f"tool:{mode}",
                TARGETS_MS["tool"],
                page.locator(f"[data-testid='viewer-tool-{mode}']"),
                lambda mode=mode: page.wait_for_function(
                    """mode => window.__neurocadeViewerDebug?.getState?.().activeDragMode === mode""",
                    arg=mode,
                    timeout=5_000,
                ),
            )

        # View switching: axial, sagittal, coronal, grid, 3D.
        for view_mode in ("axial", "sagittal", "coronal", "multi", "render"):
            _measure_control_click(
                page,
                timings,
                f"view:{view_mode}",
                TARGETS_MS["view"],
                page.locator(f"[data-testid='viewer-view-{view_mode}']"),
                lambda view_mode=view_mode: page.wait_for_function(
                    """viewMode => window.__neurocadeViewerDebug?.getState?.().activeViewMode === viewMode""",
                    arg=view_mode,
                    timeout=5_000,
                ),
            )

        # Layer controls are slice-view interactions; return to the default
        # multi-slice layout after exercising view switching.
        page.locator("[data-testid='viewer-view-multi']").evaluate("element => element.click()")
        page.wait_for_function(
            """() => window.__neurocadeViewerDebug?.getState?.().activeViewMode === 'multi'""",
            timeout=5_000,
        )

        # Show/hide an already-loaded image/segmentation layer.
        layer_items = page.locator("[data-testid='viewer-layer-item']")
        assert layer_items.count() > 0, "No viewer layers were available"
        toggle_index = None
        for index in range(layer_items.count()):
            layer_type = layer_items.nth(index).get_attribute("data-layer-type")
            toggle_label = layer_items.nth(index).locator("[data-testid='viewer-layer-visibility']").get_attribute("aria-label") or ""
            if layer_type in {"intensity", "segmentation"} and toggle_label.startswith("Hide "):
                toggle_index = index
                break
        assert toggle_index is not None, "No visible image/segmentation layer was available to hide"
        toggle_item = layer_items.nth(toggle_index)
        toggle_id = toggle_item.get_attribute("data-layer-id")
        assert toggle_id, "Selected toggle layer did not expose a layer id"
        toggle_visibility = page.locator(
            f'[data-testid="viewer-layer-item"][data-layer-id="{toggle_id}"] [data-testid="viewer-layer-visibility"]'
        )
        was_visible = True

        _measure(
            timings,
            "toggle:image-or-segmentation",
            TARGETS_MS["toggle_loaded_layer"],
            lambda: toggle_visibility.click(),
            lambda: page.wait_for_function(
                """args => window.__neurocadeViewerDebug?.getState?.().visibleLayerIds.includes(args.id) === args.visible""",
                arg={"id": toggle_id, "visible": not was_visible},
                timeout=5_000,
            ),
        )
        page.wait_for_function(
            """id => document.querySelector(`[data-testid="viewer-layer-item"][data-layer-id="${id}"] [data-testid="viewer-layer-visibility"]`)?.getAttribute('aria-label')?.startsWith('Show ')""",
            arg=toggle_id,
            timeout=10_000,
        )
        _measure(
            timings,
            "toggle:image-or-segmentation:restore",
            TARGETS_MS["toggle_loaded_layer"],
            lambda: toggle_visibility.click(),
            lambda: page.wait_for_function(
                """args => window.__neurocadeViewerDebug?.getState?.().visibleLayerIds.includes(args.id) === args.visible""",
                arg={"id": toggle_id, "visible": was_visible},
                timeout=15_000,
            ),
        )

        # Show/hide a surface if the deterministic case has one.
        surface_items = page.locator("[data-testid='viewer-layer-item'][data-layer-type='surface']")
        surface_index = None
        for index in range(surface_items.count()):
            toggle_label = surface_items.nth(index).locator("[data-testid='viewer-layer-visibility']").get_attribute("aria-label") or ""
            if toggle_label.startswith("Hide "):
                surface_index = index
                break
        if surface_index is not None:
            surface_item = surface_items.nth(surface_index)
            surface_id = surface_item.get_attribute("data-layer-id")
            assert surface_id
            surface_visibility = page.locator(
                f'[data-testid="viewer-layer-item"][data-layer-id="{surface_id}"] [data-testid="viewer-layer-visibility"]'
            )
            surface_was_visible = True
            try:
                _measure(
                    timings,
                    "toggle:surface",
                    TARGETS_MS["toggle_loaded_layer"],
                    lambda: surface_visibility.click(),
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
                    lambda: surface_visibility.click(),
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
        available_windowings = _debug_state(page)["windowings"]
        intensity_item = None
        intensity_id = None
        for index in range(intensity_items.count()):
            candidate = intensity_items.nth(index)
            candidate_id = candidate.get_attribute("data-layer-id")
            if candidate_id in available_windowings:
                intensity_item = candidate
                intensity_id = candidate_id
                break
        assert intensity_item is not None and intensity_id, "No loaded intensity layer exposed windowing controls"
        intensity_item.locator(".nc-layer-drag-handle").click()
        page.locator("[data-testid='viewer-window-min']").first.wait_for(state="visible", timeout=10_000)
        before_window = _debug_state(page)["windowings"].get(intensity_id)
        assert before_window, "Windowing readback was unavailable"
        original_min = before_window["calMin"]
        before_opacity = _debug_state(page)["layerOpacities"].get(intensity_id)
        assert isinstance(before_opacity, (int, float)), "Opacity readback was unavailable"
        next_opacity = round(max(0, before_opacity - 0.05), 2) if before_opacity > 0.5 else round(min(1, before_opacity + 0.05), 2)

        try:
            _measure(
                timings,
                "contrast:window-min",
                TARGETS_MS["windowing"],
                lambda: page.evaluate(
                    """() => {
                      const input = document.querySelector('[data-testid="viewer-window-min"]');
                      if (!(input instanceof HTMLInputElement)) throw new Error('Window minimum slider was unavailable');
                      const step = Number(input.step) || 0.01;
                      const max = Number(input.max);
                      const next = Math.min(max, Number(input.value) + step);
                      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                      setter?.call(input, String(next));
                      input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
                      input.dispatchEvent(new Event('change', { bubbles: true }));
                    }"""
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

        try:
            _measure(
                timings,
                "opacity:layer-slider",
                TARGETS_MS["opacity"],
                lambda: page.evaluate(
                    """value => {
                      const input = document.querySelector('[data-testid="viewer-layer-opacity"]');
                      if (!(input instanceof HTMLInputElement)) throw new Error('Opacity slider was unavailable');
                      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                      setter?.call(input, String(value));
                      input.dispatchEvent(new Event('input', { bubbles: true }));
                      input.dispatchEvent(new Event('change', { bubbles: true }));
                      input.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
                    }""",
                    next_opacity,
                ),
                lambda: page.wait_for_function(
                    """args => {
                      const value = window.__neurocadeViewerDebug?.getState?.().layerOpacities[args.id];
                      return typeof value === 'number' && Math.abs(value - args.originalOpacity) > 0.001;
                    }""",
                    arg={"id": intensity_id, "originalOpacity": before_opacity},
                    timeout=5_000,
                ),
            )
        except Exception as error:  # noqa: BLE001 - report diagnostic and continue timing the remaining interactions.
            interaction_failures.append({"name": "opacity:layer-slider", "error": str(error)})

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
        _measure_control_click(
            page,
            timings,
            "context:terminal-first-open",
            TARGETS_MS["first_context_tab"],
            page.locator("button:has-text('Terminal')"),
            lambda: page.locator("[data-testid='terminal-content']").wait_for(state="visible", timeout=10_000),
        )

        # Cases back navigation stays part of this timing report because the
        # original slowdown was observed returning from a case to cases.
        def wait_for_cases() -> None:
            page.wait_for_url("**/workspaces/*/cases", timeout=15_000)
            page.locator("input[placeholder='Filter cases...']").wait_for(state="visible", timeout=15_000)

        _measure_control_click(
            page,
            timings,
            "navigation:back-to-cases",
            TARGETS_MS["cases_back"],
            page.locator("[data-testid='case-workspace-back']"),
            wait_for_cases,
        )
    finally:
        api_5xx = [event for event in network_events if event.get("status", 0) >= 500 and "/api/" in event.get("url", "")]
        console_errors = [
            message for message in console_messages
            if message["type"] == "error"
            and "WebGL" not in message["text"]
            and "Failed to initialize webgpu view" not in message["text"]
        ]
        slow_api = [
            event for event in network_events
            if event.get("duration_ms") is not None
            and event["duration_ms"] > 1000
            and "/api/" in event.get("url", "")
        ]
        interaction_network_events = network_events[settled_network_cutoff:]
        slow_api_during_interactions = [
            event for event in interaction_network_events
            if event.get("duration_ms") is not None
            and event["duration_ms"] > 1000
            and "/api/" in event.get("url", "")
        ]
        report = {
            "app_url": APP_URL,
            "settle_ms": int(os.environ.get("NEUROCADE_TIMING_SETTLE_MS", "15000")),
            "interaction_click_method": "visible control locator.click(force=True, no_wait_after=True)",
            "targets_ms": TARGETS_MS,
            "timings": timings,
            "skipped": skipped,
            "interaction_failures": interaction_failures,
            "startup_network_event_count": settled_network_cutoff,
            "interaction_network_event_count": len(interaction_network_events),
            "slow_api_over_1000ms": slow_api,
            "slow_api_during_interactions_over_1000ms": slow_api_during_interactions,
            "api_5xx": api_5xx,
            "page_errors": page_errors,
            "console_errors": console_errors,
        }
        (output_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (output_dir / "console.json").write_text(json.dumps(console_messages, indent=2), encoding="utf-8")
        (output_dir / "network.json").write_text(json.dumps(network_events, indent=2), encoding="utf-8")
        context.close()
        if disposable_workspace_id:
            delete_workspace_via_api(disposable_workspace_id, app_url=APP_URL)

    assert not page_errors, f"Page errors during timing run: {page_errors}"
    assert not api_5xx, f"API 5xx responses during timing run: {api_5xx}"
    assert not console_errors, f"Console errors during timing run: {console_errors[:5]}"
