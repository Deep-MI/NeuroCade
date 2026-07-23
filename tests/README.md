# NeuroCade Tests

Unit, integration, API E2E, and browser E2E tests that validate the full NeuroCade stack: API runtime → Docker-backed tools → GUI.

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
- Assistant orchestration, streamed turns, persisted history, file tools, runtime tools, LUT lookup, and Docker runtime handoff: `test_assistant_runtime.py`, `test_assistant_turn_streaming_routes.py`, `test_assistant_history.py`, `test_assistant_file_tools.py`, `test_runtime_service_tools.py`, `test_lut_lookup.py`, `test_docker_runtime.py`
- Workspaces, cases, artifacts, scan indexing, sample seeding, admin reset, and app runtime routes: `test_workspace_routes.py`, `test_workspace_batch.py`, `test_artifact_routes.py`, `test_case_resolver.py`, `test_scan_indexing.py`, `test_bootstrap_seed.py`, `test_admin_reset.py`, `test_app_runtime_routes.py`
- API E2E tests against the running app: `test_chat_simple.py`, `test_agent_run_e2e.py`, `test_fastsurfer_run_e2e.py`; `test_mri_info_e2e.py` is skipped unless an `mri_info` runtime tool is configured.
- Browser E2E tests with Playwright: `test_gui_upload_run.py`, `test_gui_agent_run.py`, `test_gui_focus_label.py`, `test_gui_dicom_upload.py`, `test_gui_mri_header_alignment.py`

## Running Tests

### All unit tests (fast, no services needed)
```bash
source .venv/bin/activate
pytest tests/test_runtime_service_tools.py tests/test_assistant_file_tools.py tests/test_assistant_runtime.py tests/test_app_architecture.py -v
pyright
```

### Smoke tests (requires the Docker app)
```bash
source .venv/bin/activate
pytest tests/test_chat_simple.py -v
```

### API E2E tests (requires the Docker app)
```bash
source .venv/bin/activate
pytest tests/test_agent_run_e2e.py tests/test_fastsurfer_run_e2e.py -v
```

### GUI tests (headless)
```bash
source .venv/bin/activate
pytest tests/test_gui_upload_run.py tests/test_gui_focus_label.py tests/test_gui_agent_run.py -v
```

### GUI tests with visible browser
```bash
source .venv/bin/activate
HEADED=1 pytest tests/test_gui_upload_run.py -v
```

### Everything
```bash
source .venv/bin/activate
pytest tests/ -v
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_URL` | `http://localhost:8000` | Local app URL |
| `API_TOKEN` | `static-token-12345` | Bearer token for API requests |
| `HEADED` | (unset) | Set to `1` or `true` to show the Playwright browser |

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
