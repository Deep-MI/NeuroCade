# Contributing To NeuroCade

This file keeps contributor-facing project notes in one place while the
standalone documentation site is disabled.

## Development Setup

Frontend commands run from `client/`:

Node `^20.19.0`, `^22.13.0`, or `>=24` and npm `>=11.10.0` are expected.

```bash
npm install
npm run lint
npm run build
```

Backend and test setup:

```bash
uv venv --project . .venv
source .venv/bin/activate
uv sync --locked --extra test
```

Database migrations use Alembic with config at `config/alembic.ini`:

```bash
source .venv/bin/activate
alembic -c config/alembic.ini upgrade head
```

The current pre-beta schema is a clean baseline and does not upgrade databases
created by earlier development builds. Reset local application state before
testing a new baseline with `./scripts/admin/reset_app_state.sh --yes`.

Focused backend/runtime checks:

```bash
source .venv/bin/activate
pytest tests/test_neuroimaging_workflows.py tests/test_monolith_runtime.py tests/test_assistant_file_tools.py tests/test_assistant_runtime.py tests/test_app_architecture.py -v
ruff check .
pyright
```

Full Python checks:

```bash
source .venv/bin/activate
ruff check .
pytest tests -q
```

Local Docker checks:

```bash
./scripts/run.sh build
./scripts/run.sh start -d
```

## Project Boundaries

- Browser-facing routers under `api-service/api_service/routers/` should depend
  on policy helpers and service/facade modules, not other routers.
- Assistant orchestration enters through `api_service.assistant.runtime`.
  Planner coercion, prompts, history persistence, and workspace tool catalog
  logic should stay in dedicated helper modules under `api_service/assistant/`.
- Neuroimaging worker entrypoints enter through
  `api_service.runtime.neuroimaging_tasks`. Catalog parsing, workflow execution,
  and artifact indexing stay in focused runtime-tool modules.
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
  execution should route through workflows in `config/neuroimaging_tools.yaml`.

## Assistant Tooling

The web assistant exposes local Pydantic tool schemas from `api-service`.

Contributor-owned tool groups:

- Catalog tools: `tool_search`, `tool_inspect`, `tool_call`,
  `tool_run_status`, `tool_run_cancel`.
- Workspace tools: `workspace_list_cases`, `workspace_case_file_tree`,
  `workspace_file_tree`.
- Scope-limited text file tools: `read`, `write`, `edit`.
- Case/runtime tools: `freesurfer_lut`, `read_stats`, `case_file_tree`.
- Dynamic GUI tools: `gui_list_layers`, `gui_load_layer`,
  `gui_set_layer_visibility`, `gui_set_layer_display`, `gui_remove_layer`,
  `gui_reorder_layer`, `gui_apply_view_preset`, `gui_move_cursor`,
  `gui_focus_label`.

Do not reintroduce MCP server dependencies for assistant tools.

### Assistant Context Contract

At the start of each assistant turn, the runtime resolves its tools, GUI snapshot,
and workspace summaries, then builds one immutable system-prompt snapshot reused
for every model/tool round in that turn. Every model call has exactly one system
message followed by conversation messages and one final response-contract
message. The system message contains labeled blocks in this order:

1. `<assistant_role>`: complete `config/SOUL.md` content.
2. `<response_policy>`: the structured-response and evidence rules implemented by
   the orchestration loop.
3. `<available_tools>`: every tool currently registered for the request scope,
   with its description and compact JSON parameter schema.
4. `<session_context>`: authorization scope, workspace/case identifiers, bounded
   GUI state, applicable path rules, and bounded workspace case summaries.
5. `<system_information>`: complete `config/INFORMATION.md` content.
6. `<operating_rules>`: complete `config/RULES.md` content.

The three prompt files are required and must be non-empty. They are never sliced.
Keep them concise and put changing capability details in tool descriptions or
`config/neuroimaging_tools.yaml`, not in static prompt prose.

Context bounds are deliberate and visible to the model:

- GUI context includes at most 50 layers and reports `layer_count` and
  `layers_omitted`.
- Workspace context includes at most 50 case summaries and reports `case_count`
  and `cases_omitted`; the model can call `workspace_list_cases` for the complete
  current list.
- Private thread history keeps the newest configured message/character window
  (`ASSISTANT_HISTORY_MAX_MESSAGES`, `ASSISTANT_HISTORY_MAX_CHARACTERS`) and adds a
  context notice whenever older material is omitted or compacted.
- The final request prompt keeps the complete system and response-contract
  messages, then prioritizes the newest conversation within
  `ASSISTANT_PROMPT_MAX_CHARACTERS`. Any reduction adds a context notice; a limit
  too small for the required fixed context fails explicitly instead of slicing it.
- Individual tool results passed back to the model retain their beginning and end
  up to 40,000 characters with an omission marker. Tool results stored for UI
  history retain up to 8,000 characters with the same marker.
- Current-turn image content is sent as structured multimodal content. Persisted
  history replaces image data with a text marker rather than storing or replaying
  the original data URL.

Tool results are converted to user-role messages wrapped in `<tool_output>` and
explicitly labeled untrusted data. Native user messages remain user messages;
assistant history remains assistant messages. The final message contains only the
JSON response contract from `assistant/structured_response.py`.

## Runtime Tools

The monolith runs in one Docker container. Neuroimaging workflows are defined
authoritatively in `config/neuroimaging_tools.yaml` and loaded on demand:

```bash
./scripts/run.sh status
./scripts/run.sh logs
```

## Release Checklist

Before tagging or publishing a release:

- Confirm the release branch contains only intended changes.
- Confirm no secrets are committed in `.env`, logs, screenshots, or local data.
- Review `.env.example`.
- Confirm `DEPLOYMENT_PROFILE` is one of `local`, `internal`, or `demo`.
- Confirm `APP_BASE_URL`, `APP_PUBLIC_URL`, and `APP_ALLOWED_HOSTS` match the
  deployed origin.
- Confirm `LOCAL_AUTH_ENABLED=false` for `internal` and `demo`.
- Confirm Clerk, LLM, Docker runtime, and monitoring settings are correct for
  the deployment profile.
- Run frontend lint/build and focused backend/runtime tests.
- Run the full Python test suite when release time allows.
- Confirm `./scripts/build_image.sh` completes.
- Confirm runtime commands run rootless (no `--fakeroot`/`--writable`) unless GPU/runtime settings explicitly require otherwise.
- Confirm artifact download routes require authorization.
- Build the Docker image.

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
./scripts/install.sh --mode <profile> --yes
./scripts/run.sh start -d
```

Useful logs:

- `./scripts/run.sh logs` (or `docker logs -f neurocade`)
- App logs from the single `neurocade` container

Backups should include `.env` and `neurocade-data`.

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
