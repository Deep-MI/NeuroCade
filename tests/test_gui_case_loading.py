"""Browser regression tests for routed case loading states."""

from urllib.parse import urlparse

from gui_helpers import APP_URL

pytest_plugins = ["conftest_gui"]


def test_sample_case_shows_loading_state_until_artifacts_arrive(page):
    """Do not show the empty-case prompt while a routed case is loading."""
    parts = urlparse(page.url).path.strip("/").split("/")
    assert len(parts) >= 4 and parts[0] == "workspaces", f"Expected case route, got {page.url}"
    workspace_id = parts[1]
    sample_case_id = parts[3]

    page.goto(f"{APP_URL}/workspaces/{workspace_id}/cases", wait_until="domcontentloaded", timeout=30_000)
    sample_case = page.get_by_test_id(f"workspace-case-title-{sample_case_id}")
    sample_case.wait_for(state="visible", timeout=20_000)

    page.evaluate(
        """() => {
          const originalFetch = window.fetch.bind(window);
          window.fetch = async (...args) => {
            const requestUrl = String(args[0] instanceof Request ? args[0].url : args[0]);
            if (requestUrl.includes('/api/app/cases/') && requestUrl.endsWith('/artifacts')) {
              await new Promise((resolve) => window.setTimeout(resolve, 1500));
            }
            return originalFetch(...args);
          };
        }"""
    )

    sample_case.click()
    page.wait_for_url(f"**/workspaces/*/cases/{sample_case_id}", timeout=10_000)
    page.locator(".nc-viewer-canvas-spinner").wait_for(state="visible", timeout=5_000)
    assert page.locator(".nc-viewer-canvas-status").count() == 0

    page.locator("[data-testid='viewer-layer-item']").first.wait_for(state="visible", timeout=30_000)
    assert page.locator(".nc-viewer-canvas-status").count() == 0
