# Contributing To NeuroCade

This file keeps contributor-facing project notes in one place while the
standalone documentation site is disabled.

## Development Setup

Frontend commands run from `client/`:

Node `>=20` and npm `>=11.10.0` are expected.

```bash
npm install
npm run lint
npm run build
```

Backend and test setup:

```bash
uv venv --project . .venv
source .venv/bin/activate
uv pip install -r pyproject.toml --extra test
```

Database migrations use Alembic with config at `config/alembic.ini`:

```bash
source .venv/bin/activate
alembic -c config/alembic.ini upgrade head
```

Focused backend/runtime checks:

```bash
source .venv/bin/activate
pytest tests/test_runtime_service_tools.py tests/test_assistant_file_tools.py tests/test_assistant_runtime.py tests/test_app_architecture.py -v
pyright
```

Full Python checks:

```bash
source .venv/bin/activate
pytest tests -q
pytest packages/neurocade-runtime-tools/tests -q
```

Local Apptainer checks:

```bash
./scripts/apptainer/images.sh preflight
./scripts/containers.sh refresh-index
./scripts/apptainer/up.sh -d
```

## Project Boundaries

- Browser-facing routers under `api-service/api_service/routers/` should depend
  on policy helpers and service/facade modules, not other routers.
- Assistant orchestration enters through `api_service.assistant.runtime`.
  Planner coercion, prompts, history persistence, and workspace tool catalog
  logic should stay in dedicated helper modules under `api_service/assistant/`.
- Workspace batch worker entrypoints enter through `api_service.workspace_batch`.
  Query, filesystem/mount preparation, and report/artifact synchronization
  should stay in helper modules beneath that facade.
- `CaseWorkspace.tsx` should remain a page/container. Upload, run control,
  polling, navigation, chat notifications, and viewer volume mutations belong in
  hooks or focused child components.

## Backend Guardrails

- Canonical access checks live in `api_service.policies`.
- New route code should use capability helpers such as
  `require_workspace_read`, `require_workspace_write`,
  `require_workspace_manage`, `require_case_read`, `require_case_write`, and
  `require_case_manage`.
- Avoid inline role-set checks in routers.
- Avoid router-to-router imports and task/worker imports from router modules.
- Keep unrestricted shell/Python execution out of the web assistant. Container
  execution should route through approved runtime tools and the local installed-tool
  index.

## Assistant Tooling

The web assistant exposes local Pydantic tool schemas from `api-service`.

Contributor-owned tool groups:

- Installed runtime tools: `tool_search`, `tool_call`.
- Workspace tools: `workspace_list_cases`, `workspace_case_file_tree`,
  `workspace_file_tree`, `workspace_probe_bash`, `workspace_bash`,
  `workspace_batch_bash`, `workspace_list_batch_runs`,
  `workspace_cancel_batch_run`.
- Case/runtime tools: `freesurfer_lut`, `read_stats`, `case_file_tree`.
- Dynamic GUI tools, depending on viewer state: `gui_run_fastsurfer`,
  `gui_review_segmentation`, `gui_load_volume`, `gui_close_volume`,
  `gui_select_volume`, `gui_adjust_display`, `gui_move_cursor`,
  `gui_focus_label`.

Do not reintroduce MCP server dependencies for assistant tools.

## Runtime Tool Index

Runtime container status and the generated installed-tool index are managed locally:

```bash
./scripts/containers.sh status --json
./scripts/containers.sh refresh-index
```

## Release Checklist

Before tagging or publishing a release:

- Confirm the release branch contains only intended changes.
- Confirm no secrets are committed in `.env`, logs, screenshots, or local data.
- Review `.env.example` and `.env.local.example`.
- Confirm `DEPLOYMENT_PROFILE` is one of `local`, `internal`, or `demo`.
- Confirm `APP_BASE_URL`, `APP_PUBLIC_URL`, and `APP_ALLOWED_HOSTS` match the
  deployed origin.
- Confirm `LOCAL_AUTH_ENABLED=false` for `internal` and `demo`.
- Confirm Clerk, Postgres, Redis, LLM, managed container, and monitoring settings are correct
  for the deployment profile.
- Run frontend lint/build and focused backend/runtime tests.
- Run the full Python and runtime-tools test suites when release time allows.
- Confirm Apptainer preflight passes without sudo or Docker socket access.
- Confirm runtime commands use no-network Apptainer/Singularity execution.
- Confirm artifact download routes require authorization.
- Build or fetch infrastructure SIF images and verify release checksums.
- Install managed runtime containers and refresh the installed-tool index.

Smoke tests:

- `local`: upload an MRI, start a run, cancel a run, view logs, and download a
  case.
- `internal`: sign in through Clerk, inspect sample data, upload a
  de-identified MRI, start/cancel a run, and verify monitoring as an admin.
- `demo`: inspect sample data and verify uploads, run starts, renames, deletes,
  cancels, workspace mutations, and workflow approvals are blocked.

## Deployment Notes For Contributors

Profiles:

- `local`: single-user workstation install. Vite is allowed for convenience and
  uploads are enabled.
- `internal`: authenticated institutional service for de-identified research
  MRI. Uploads are enabled, Clerk is required, and monitoring is admin-only.
- `demo`: public sample-data instance. Uploads, run starts, deletes, renames,
  and cancels are disabled by deployment policy.

Install/update flow:

```bash
./scripts/install.sh --doctor --mode <profile> --yes
./scripts/apptainer/images.sh preflight
./scripts/containers.sh refresh-index
./scripts/apptainer/up.sh -d
```

Useful logs:

- `./scripts/apptainer/logs.sh -f`
- `.runtime/logs/api-service.log`
- `.runtime/logs/api-worker.log`
- `.runtime/logs/traefik.log`

Backups should include Postgres, `.env`, `.runtime`, and `neurocade-data`.

## GitHub Repository Settings

Protect `main` and release branches. Recommended required checks:

- `Frontend lint, test, build`
- `Backend and security tests`
- `NeuroCade runtime tools tests`
- `Repository policy checks`
- CodeQL Python and JavaScript/TypeScript analysis

Recommended options:

- Require pull requests before merging.
- Require at least one approval.
- Dismiss stale approvals after new commits.
- Require status checks before merging.
- Require branches to be up to date before merging.
- Require conversation resolution.
- Restrict force pushes and deletions.

Keep vulnerability scans advisory until scientific Python and container-stack
findings have been reviewed and intentionally promoted to release blockers.
