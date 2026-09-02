# NeuroCade Tests

The default suite contains fast unit and integration contracts. Full-stack API, browser, and live-model checks live under `tests/evaluations/` and run only when explicitly selected.

## Prerequisites

1. **Docker app running** (for API E2E and GUI tests):
   ```bash
   ./scripts/run.sh start -d
   ```

2. **Seed test data present:**
   - At least one MRI file under `tests/fixtures/uploads/`, or set `NEUROCADE_UPLOAD_FIXTURES_DIR`
   - At least one processed FastSurfer case under `$HOST_DATA_DIR/output/`, or set `NEUROCADE_TEST_OUTPUTS_DIR`
   - The E2E fixtures now copy that seed upload/output pair into a fresh workspace and case for each module, so the tests no longer mutate the shared demo record in place.

3. **Python dependencies:**
   ```bash
   uv venv --project . .venv && source .venv/bin/activate
   uv sync --locked --extra test
   playwright install chromium   # only for GUI tests
   ```

4. **Resetting to a clean local baseline (optional but useful for debugging):**
   ```bash
   ./scripts/admin/reset_app_state.sh
   ```
   That recreates `$HOST_DATA_DIR/output/` while preserving the runtime license file. If you also want to clear user-created workspaces in the app database, follow with:
   ```bash
   source .venv/bin/activate
   python scripts/admin/reset_user_workspaces.py --help
   ```

---

## Test Areas

The suite changes often, so prefer checking file names with:

```bash
rg --files tests -g 'test_*.py' | sort
```

Current coverage is organized around these areas:

- Backend architecture, settings, auth, security, monitoring, and install policy: `test_app_architecture.py`, `test_security_hardening.py`, `test_monitoring_routes.py`, `test_install_scripts.py`, `test_chat_limits.py`
- Assistant orchestration, streamed turns, persisted history, file tools, runtime tools, LUT lookup, and container runtime handoff: `test_assistant_harness_p0.py`, `test_assistant_turn_streaming_routes.py`, `test_assistant_history.py`, `test_assistant_file_tools.py`, `test_gui_runtime_tools.py`, `test_lut_lookup.py`, `test_monolith_runtime.py`, `test_runtime_execution.py`
- Workspaces, cases, artifacts, filesystem reconciliation, sample seeding, admin reset, and app runtime routes: `test_workspace_routes.py`, `test_artifact_routes.py`, `test_case_resolver.py`, `test_bootstrap_seed.py`, `test_admin_reset.py`, `test_app_runtime_routes.py`
- API full-stack evaluations against the running app: `evaluations/eval_agent_run.py` and `evaluations/eval_fastsurfer_run.py`.
- Browser evaluations under `tests/evaluations/` use the `gui` marker. Checks that contact a configured model also use `live_llm`.
- Viewer performance analysis lives in `viewer_timing_benchmark.py` and runs only through `scripts/analyze_viewer_timing.sh`.

## Running Tests

### Core tests (fast, no services needed)
```bash
source .venv/bin/activate
pytest tests -v
ruff check .
pyright
```

### Smoke tests (requires the Docker app)
```bash
curl http://localhost:8000/api/app/healthz
```

### API E2E tests (requires the Docker app)
```bash
source .venv/bin/activate
pytest tests/evaluations/eval_*.py -m e2e -v
```

### GUI tests (headless)
```bash
source .venv/bin/activate
pytest tests/evaluations/eval_gui_*.py -m "gui and not live_llm" -v
```

Tests that send prompts to a live LLM are skipped by default so the regular
suite does not depend on an external model service. Enable them explicitly
when the configured backend is reachable:

```bash
RUN_LLM_E2E=1 pytest tests/evaluations/eval_*.py -m live_llm -v
```

### GUI tests with visible browser
```bash
source .venv/bin/activate
HEADED=1 pytest tests/evaluations/eval_gui_*.py -m "gui and not live_llm" -v
```

### Run every evaluation tier
```bash
source .venv/bin/activate
pytest tests/evaluations/eval_*.py -v
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_URL` | `http://localhost:8000` | Local app URL |
| `APP_AUTH_TOKEN` | (unset) | Optional bearer token for authenticated API and browser tests |
| `HEADED` | (unset) | Set to `1` or `true` to show the Playwright browser |
| `RUN_LLM_E2E` | (unset) | Set to `1` to run browser tests that invoke the configured live LLM |

## Screenshots

GUI tests save screenshots to `tests/screenshots/`. These are useful for debugging CI failures or reviewing visual state.

## Troubleshooting

1. **Tests skip with "NeuroCade stack not reachable":**
   ```bash
   ./scripts/run.sh status   # check the app is running
   curl http://localhost:8000/api/app/healthz  # check the app
   ```

2. **GUI tests fail with "Playwright not installed":**
   ```bash
   uv sync --locked --extra test
   playwright install chromium
   ```

3. **LLM gives text explanation instead of calling a tool:**
   - Check API and runtime logs: `./scripts/run.sh logs`
   - Restart after code changes: `./scripts/run.sh stop && ./scripts/run.sh start -d`
