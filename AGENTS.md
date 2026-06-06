# Repository Guidelines

## Project Structure & Module Organization
- `client/`: React 19 + TypeScript UI and Electron desktop launcher. Main browser entry points are `src/main.tsx` and `src/App.tsx`; reusable UI lives in `src/components/`; pages in `src/pages/`; hooks in `src/hooks/`; shared types and API helpers in `src/types.ts`, `src/constants.ts`, and `src/utils/`. Electron code lives in `client/electron/`.
- `api-service/`: FastAPI backend, assistant orchestration, local runtime tools, and Celery tasks. Browser-facing routers live in `api_service/routers/`. Assistant code lives in `api_service/assistant/`, runtime orchestration in `api_service/runtime/`, runtime tool handlers in `api_service/runtime_tools/`, cases in `api_service/cases/`, monitoring in `api_service/monitoring/`, and workspace batch workflows in `api_service/workspace_batch/`.
- `backend_common/`: shared backend utilities for settings, auth, database access, providers, storage, scan metadata, workspace bootstrap, and cross-service helpers.
- `packages/neurocade-runtime-tools/`: local runtime container management, installed-tool index generation, and Apptainer routing helpers.
- `tests/`: `pytest` suite covering unit, integration, API E2E, and Playwright browser flows.
- `migrations/`: Alembic database migrations; config is in `config/alembic.ini`.
- `config/` and `scripts/`: project configuration, rootless Apptainer launchers, install helpers, desktop helpers, release scripts, and admin reset tools.
- `neurocade-data/`: default local `HOST_DATA_DIR` for runtime data, uploads, generated outputs, and `license.txt`. Outputs live under `$HOST_DATA_DIR/output`.
- `.runtime/`, `.apptainer/`, `llm-data/`, and `client/dist/`: generated local runtime/build artifacts. Do not treat these as source unless the task explicitly targets generated state.

## Architecture Overview
Traefik serves the app on `http://localhost:8005`. The main frontend targets `/api/app` as the browser-facing API boundary, while the API service talks to Postgres, Redis, and the local runtime tools package. The Electron launcher in `client/electron/` wraps the local web app and can start the Apptainer backend for local desktop use.

Assistant orchestration enters through `api_service.assistant.runtime`, with prompts, history, planner logic, tool registry, workspace tools, and turn streaming kept in focused modules under `api_service/assistant/`. Pydantic-style runtime tool dispatch runs in-process inside `api-service` and routes approved container commands through `api_service/runtime_tools/` and `packages/neurocade-runtime-tools/`. Long-running workspace and FastSurfer jobs move to `api-worker` through Celery queues (`workspace_batch`, `fastsurfer`) with Redis, outputs land under `$HOST_DATA_DIR/output`, and Postgres is the application source of truth for metadata and authorization. Database bootstrapping uses Alembic migrations from `migrations/`.

## Build, Test, and Development Commands
Frontend commands run from `client/`:

- Node `>=20` and npm `>=11.10.0` are expected.
- `npm install`: install frontend and Electron dependencies.
- `npm run dev`: start the Vite dev server.
- `npm run build`: type-check and produce a production bundle.
- `npm run lint`: run ESLint on `ts` and `tsx` sources.
- `npm run electron:local`: start the Electron launcher directly from `client/`.

Python and integration work should start with the project configuration:

- `.env`: repository config file for Apptainer, backend, and token settings. It is not a Python virtualenv.
- `uv venv --project . .venv && source .venv/bin/activate`: create and activate the local project virtualenv from `pyproject.toml`.
- `uv pip install -r pyproject.toml --extra test`: install Python dependencies into `.venv`.
- `source .venv/bin/activate && playwright install chromium`: install browser binaries needed for GUI tests.
- `source .venv/bin/activate && alembic -c config/alembic.ini upgrade head`: apply database migrations manually when needed.
- `source .venv/bin/activate && pytest tests/test_runtime_service_tools.py tests/test_assistant_file_tools.py tests/test_assistant_runtime.py tests/test_app_architecture.py -v`: run focused backend/runtime tests from the local `.venv`.
- `source .venv/bin/activate && pytest packages/neurocade-runtime-tools/tests -q`: run runtime-tools package tests.
- `source .venv/bin/activate && pyright`: run Python type checks.
- `./scripts/apptainer/up.sh -d`: start the rootless local stack required for API and GUI tests.
- `./scripts/apptainer/images.sh preflight`: check rootless Apptainer runtime and fakeroot/build capability.
- `./scripts/containers.sh refresh-index`: refresh the installed container/tool catalog after manual container changes.
- `./scripts/desktop/run.sh`: run the local desktop launcher from the repository root.
- `./scripts/install.sh --mode local --desktop`: perform the local install flow and prepare the desktop launcher.
- `source .venv/bin/activate && pytest tests/ -v`: run the full test suite.
- `source .venv/bin/activate && HEADED=1 pytest tests/test_gui_upload_run.py -v`: run a browser test with a visible window.

## Apptainer Setup Notes
Core services are `gateway`, `client`, `api-service`, `api-worker`, `postgres`, and `redis`. Keep `.env` aligned before startup, especially `HOST_DATA_DIR`, managed container/index paths, `LLM_BACKEND_URL`, Postgres/Redis settings, auth/provider tokens, and `FREESURFER_LICENSE`.
Runtime tool containers are managed with `./scripts/containers.sh`; use `./scripts/containers.sh refresh-index` after manual container changes. The installed container and tool catalogs live under `llm-data/tool-catalog/`.
The install and runtime path must remain rootless. Do not add sudo setup, Docker socket access, or root-owned output paths. Apptainer runs should write files as the invoking host user.
Use `./scripts/admin/reset_app_state.sh` for local reset/debugging instead of manually deleting database or runtime data; it preserves `license.txt`.

## Backend Boundaries
- Browser-facing route modules under `api-service/api_service/routers/` should depend on policy helpers, service modules, and shared `backend_common` utilities, not on other routers.
- Keep canonical access checks in `api_service.policies` and use capability helpers such as `require_workspace_read`, `require_workspace_write`, `require_workspace_manage`, `require_case_read`, `require_case_write`, and `require_case_manage`.
- Keep unrestricted shell/Python execution out of the web assistant. Container execution should route through approved runtime tools and the local installed-tool index.
- Worker entrypoints should stay in `api_service.workspace_batch.tasks` and `api_service.runtime.fastsurfer_tasks`; shared workflow/query/filesystem logic belongs in the corresponding package modules, not in routers.

## Coding Style & Naming Conventions
Use 2-space indentation and functional React components in `client/`. Prefer `PascalCase` for component files such as `MriViewer.tsx` and `camelCase` for helpers such as `caseStorage.ts`. Keep TypeScript strictness intact and fix lint issues before opening a PR.

Python code targets Python 3.12, uses 4-space indentation, type hints where practical, and `snake_case` for modules, functions, and fixtures. Name tests `test_*.py`.

## Testing Guidelines
`pytest` is the test runner; Playwright covers browser flows. Prefer isolated unit tests where possible, reserve Apptainer-backed runs for API/E2E coverage, and document new setup requirements in `tests/README.md`. GUI and API E2E tests expect the Apptainer stack on `http://localhost:8005` and seed data under `$HOST_DATA_DIR/output` unless overridden by test-specific environment variables.

## Commit & Pull Request Guidelines
Recent history uses short, imperative subjects, sometimes with Conventional Commit prefixes such as `feat:` and `fix:`. Follow that pattern, for example `feat: add case selector filtering` or `fix: guard missing segmentation state`.

PRs should explain user-visible impact, list the commands you ran, link related issues, and include screenshots for `client/` UI changes. Do not add `Co-Authored-By` trailers.
