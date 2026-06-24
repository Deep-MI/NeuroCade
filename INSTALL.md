# Installation

## Guided Installer

The recommended setup path is the interactive installer. It works from an
existing checkout, or as a copy-paste command that clones the repo when needed:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh)
```

Fresh one-line installs clone the latest stable release tag by default. Use
`--prerelease` to clone the latest beta release tag, or `--dev` to clone the
repository default branch.

From a checkout:

```bash
./scripts/install.sh
```

The installer guides users through three deployment profiles:

- `local`: one user on the same machine, local auth enabled, app bound to `localhost`.
- `internal`: authenticated institutional server for de-identified research MRI.
- `demo`: public sample-data instance with uploads and destructive actions disabled.

It also configures the LLM backend. Supported choices are Blablador or another
OpenAI-compatible endpoint, Anthropic, Google, and local Ollama. The installer
writes `.env`, backs up an existing `.env`, creates the local data directories,
generates service secrets, and can optionally start the stack.

For `local` mode, the installer also prepares the Electron desktop launcher by
default. After installation, users can open NeuroCade from the generated
desktop shortcut or with:

```bash
./scripts/desktop/run.sh
```

The desktop launcher starts the local Docker Compose stack when needed, waits for the
API to become healthy, opens the app window, and stops the stack on quit if it
started the stack itself. Docker Compose remains required, but backend startup is
hidden behind the desktop app after setup. On Linux, the launcher starts Electron with
Chromium sandbox fallback flags used by Electron apps on hosts where the setuid
sandbox is unavailable. Use `--no-desktop` to keep the browser-only local flow.

Useful non-interactive examples:

```bash
./scripts/install.sh --doctor --mode local --llm-provider ollama --yes
./scripts/install.sh --dry-run --mode local --llm-provider ollama --yes
./scripts/install.sh --mode local --llm-provider ollama --no-start --yes
./scripts/install.sh --mode internal --llm-provider openai-compatible
./scripts/install.sh --mode demo --llm-provider openai-compatible
./scripts/install.sh --mode local --no-desktop
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --prerelease --mode local
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --dev --mode local
```

Windows users should run the installer inside a Linux/WSL2 environment with
Docker Engine and the Compose plugin available. Native PowerShell installation is not supported in this
repository yet.

## Prerequisites

The installer checks prerequisites, installs `uv` with confirmation when it is
missing, creates `.venv` from the Python runtime declared in `pyproject.toml`,
checks Docker Compose, and still avoids changing system package managers unless
a missing runtime is required. Install any remaining missing prerequisites with
your site-approved method, then rerun it.

Main dependencies are:
- uv for the project Python runtime. The installer uses it to create `.venv`
  and install backend/runtime tooling consistently.
- Docker Engine with the Docker Compose plugin. Docker Desktop is the simplest
  path on macOS; Linux hosts should use the site-approved Docker Engine and
  Compose plugin installation.
- Node.js 20+ and npm 11.10.0+. If they are missing on macOS/Linux x64 or arm64, the
  installer downloads the official Node.js v22 binary archive into `.node/`.
- optional GPU support if you want the worker to use CUDA
- optional FreeSurfer license file for the full FastSurfer MRI pipeline

Every real installer run writes a structured log to
`.runtime/logs/install.log`. Use `--doctor` to check host readiness and
`--dry-run` to show the plan without writing `.env`, downloading runtimes, or
starting services.

## Update Checks

When the local stack starts, NeuroCade runs a quiet update checker once and then
every 24 hours while the stack keeps running. It fetches
`NEUROCADE_VERSION_CHECK_URL` as a static JSON document and compares the returned
`version`, `latest_version`, or `tag` with the local `NEUROCADE_VERSION`. If a
newer version is available, the message is written to the `update-checker`
service logs and appears in `./scripts/compose/logs.sh`.
If the endpoint is unreachable or returns invalid data, nothing is logged.

## Required Repo Configuration

Copy or edit `.env` before startup. The minimum configuration keys are:

- `NEUROCADE_HOST_DATA_DIR`: host path to the NeuroCade runtime data directory. Local installs default to `neurocade-data/`.
- `HOST_DATA_DIR`: in-container data root. Compose sets this to `/data`.
- `LLM_API_TOKEN`
- `LLM_BACKEND_URL`
- `LLM_BACKEND_API_KEY`
- `LLM_BACKEND_MODEL`
- `REDIS_PASSWORD` (defaults to `fastsurfer-dev-redis` if omitted, but setting it explicitly is better)

Leave `LLM_BACKEND_API_KEY` blank for OpenAI-compatible backends that do not
require authentication. Set it to the backend token when the endpoint expects
Bearer authentication.

Optional but commonly needed:

- `FREESURFER_LICENSE`: optional path to a valid FreeSurfer license file. The installer copies it to `neurocade-data/license.txt`, which is required for the full MRI pipeline. FreeSurfer licenses are free from https://surfer.nmr.mgh.harvard.edu/registration.html.
- `NEUROCADE_INSTALLED_TOOLS_JSONL`: optional override for the generated installed-tool index. Defaults to `llm-data/tool-catalog/installed_tools.jsonl`.
- `OLLAMA_MODEL` if you want the optional local Gemma 4 backend

Example:

```bash
NEUROCADE_HOST_DATA_DIR=/abs/path/to/neurocade-data
HOST_DATA_DIR=/data
FREESURFER_LICENSE=/abs/path/to/license.txt
REDIS_PASSWORD=fastsurfer-dev-redis
```

For shared or public deployments, use `DEPLOYMENT_PROFILE=internal` or
`DEPLOYMENT_PROFILE=demo`, keep `LOCAL_AUTH_ENABLED=false`, and configure the
Clerk values. The backend refuses to start in shared profiles with local auth
or default database/Redis credentials.

## Optional Local Gemma 4 Backend

The stack can start an optional local Ollama service for Gemma 4.

Recommended settings for a 16 GB-class GPU:

```bash
LLM_BACKEND_URL=http://127.0.0.1:11434
LLM_BACKEND_API_KEY=
LLM_BACKEND_MODEL=gemma4:e2b
OLLAMA_MODEL=gemma4:e2b
LLM_NATIVE_TOOL_CALLING=false
```

Start the app with the standard Docker Compose launcher:

```bash
./scripts/compose/up.sh -d
```

Notes:

- `gemma4:e2b` is the safest starting point on the current 16 GB GPU.
- `gemma4:e4b` may also fit, but with less headroom.
- Local Ollama support expects an Ollama service reachable at `OLLAMA_BASE_URL`; use `http://host.docker.internal:11434` when Ollama runs on the host.

## Runtime Containers And Tool Index

Docker Compose installs use pinned core Docker images and generate a core installed-tool index at startup. The installer runs:

```bash
./scripts/compose/images.sh
```

The Compose path builds the single NeuroCade app image (API + built SPA +
Apptainer) and the managed bash runtime image locally. Runtime jobs use pinned
tool images for FastSurfer, dcm2niix, and FreeSurfer, launched in-process via the
selected runtime backend (Apptainer by default, Docker for dev).

Known core containers use prebuilt tool indexes shipped with the runtime-tools
package. This avoids install-time command discovery and help harvesting for the
standard images. The FreeSurfer prebuilt index is intentionally curated instead
of indexing every executable in the image.

Docker Compose startup regenerates the pinned core Docker index automatically.
Maintainers can regenerate it explicitly with:

```bash
./scripts/compose/images.sh
```

## Sample Case Setup

The seeded app sample case is generated from a real FastSurfer run on the Rhineland T1-weighted example scan and kept entirely under [sample_case](sample_case).
Release builds attach the curated seed as
`neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz` when
`sample_case/FastSurfer_Rhineland_0000` is present. The installer downloads and
extracts that release asset automatically. If the chosen
release does not include the sample-case archive, the installer scans older
releases in the selected channel for that archive only; other release assets
still come from the chosen release. If no matching release asset is available,
the installer warns and continues without a sample case. Maintainers should
generate or refresh that directory before cutting a release, or set
`BUILD_SAMPLE_CASE_ARTIFACT=true` in a release environment that has the raw
sample data, FastSurfer container, and FreeSurfer license.

Recommended quick call from the repo root:

```bash
./scripts/process_demo_case.sh
```

That helper:

- downloads the raw Rhineland sample automatically when `sample_case/RLS_case_all/sub_rs_mri_raw/T1_RMS.nii.gz` is missing
- reuses already-downloaded raw data when present
- delegates the actual FastSurfer sample build to `sample_case/create_fastsurfer_sample_case.sh`

If you want the explicit two-step path instead:

```bash
cd sample_case
./download_sub_rs_mri_proc.sh
./create_fastsurfer_sample_case.sh
cd ..
```

Notes:

- `./download_sub_rs_mri_proc.sh` downloads only the raw Rhineland sample by default.
- `./download_sub_rs_mri_proc.sh --full` still downloads the full raw + processed + structural-only bundle if you want all reference assets locally.
- `./create_fastsurfer_sample_case.sh` runs FastSurfer on `RLS_case_all/sub_rs_mri_raw/T1_RMS.nii.gz` and rebuilds the seeded app sample case from those outputs.
- `./scripts/process_demo_case.sh --build-only` skips the downloader and just refreshes the sample case from whatever is already present locally.
- `DEVICE_MODE=auto` prefers CUDA, but it falls back to CPU automatically if the selected FastSurfer image does not support the host GPU architecture.
- the sample-case build uses `neurocade-data/license.txt`, matching the `/data/license.txt` source used by the runtime tools; if it is missing, the builder will populate it from `FREESURFER_LICENSE`
- Host-run services read `sample_case/` directly, so you do not need to rebuild images after regenerating the sample case.

## Recommended Startup Path

Use the Docker Compose repo wrapper:

```bash
./scripts/compose/up.sh -d
```

This script:

- builds the single NeuroCade app image (API + built SPA + Apptainer) when needed
- starts the one `app` container (no Postgres, Redis, gateway, worker, or runner)
- generates the pinned core installed-tool index under `llm-data/tool-catalog/`
- runs FastSurfer, FreeSurfer, dcm2niix, and workspace bash in-process via Apptainer/Docker
- stores persistent app data (including the SQLite database) under `neurocade-data/`

The application is available at:

- `http://localhost:8000`
- `http://localhost:8000/workspaces/personal-workspace/cases` for the default local workspace

For local desktop installs, users normally do not need these URLs directly; the
Electron launcher loads the local app automatically.

The app process serves both the built React client and the `/api/app` API from a
single origin (no separate gateway). Active frontend development can still use the
Vite dev server from `client/`, but the local install path serves the production
bundle.

Public demo deployments generated by the installer bind to an unprivileged
local port. Configure an external proxy to terminate TLS and forward to the
local app:

```bash
APP_HTTP_BIND=127.0.0.1
APP_HTTP_PORT=8005
APP_DOMAIN=<your-domain>
ACME_EMAIL=<admin-email>
```

Profile-specific examples are provided in `.env.example` and
`.env.local.example`.

Useful runtime commands:

```bash
./scripts/compose/images.sh
./scripts/compose/status.sh
./scripts/compose/logs.sh -f
./scripts/compose/down.sh
```

## Local Python/Test Setup

The installer prepares `.venv` with `uv`. To refresh it manually for local
scripts and tests:

```bash
uv venv --project . .venv
uv pip install --python .venv/bin/python -r pyproject.toml --extra test
```

Run a quick check:

```bash
source .venv/bin/activate
pytest tests/test_runtime_service_tools.py tests/test_assistant_file_tools.py tests/test_assistant_runtime.py tests/test_app_architecture.py -q
pyright
```

## Tool Index Setup

The assistant searches and routes installed container tools through the local
runtime index. Docker Compose startup generates a pinned core index for the
supported v1 runtime images. The generated files are local runtime data and are
not committed:

- `llm-data/tool-catalog/installed_containers.json`
- `llm-data/tool-catalog/installed_tools.jsonl`

Regenerate them after image changes with:

```bash
./scripts/compose/images.sh
```

## Application Services

The monolith is one process composed of these parts:

- `api_service` (`api-service/`): the FastAPI app — the `/api/app` API boundary, the built SPA via StaticFiles, the in-process JobWorker (`api_service/jobs/`), and assistant/runtime orchestration under `api_service/assistant/` and `api_service/runtime/`
- `backend_common`: shared settings, auth, the SQLite database, providers, storage, and sample-seeding utilities
- `packages/neurocade-runtime-tools`: runtime container request builders, the Apptainer/Docker backends, and installed-tool index generation
- `migrations`: Alembic migration history used during database bootstrap

Additional environment variables:

```bash
# DATABASE_URL defaults to a SQLite file under HOST_DATA_DIR; set it to override.
NEUROCADE_RUNTIME_BACKEND=apptainer   # or "docker" for native dev
DEPLOYMENT_PROFILE=local
LOCAL_AUTH_ENABLED=true
VITE_CLERK_PUBLISHABLE_KEY=
VITE_CLERK_JWT_TEMPLATE=
CLERK_SECRET_KEY=
CLERK_JWKS_URL=
CLERK_ISSUER=
CLERK_AUDIENCE=
LLM_PROVIDER_DEFAULT=openai-compatible
WORKFLOW_DEFAULT_PROVIDER=openai-compatible
```

In `DEPLOYMENT_PROFILE=local`, the app can use local auth so local work is not
blocked. The app serves the built React client itself. `internal` and `demo`
deployments must use `LOCAL_AUTH_ENABLED=false` and a complete Clerk
configuration.

## Runtime Architecture

- Browser-facing API routes are served under `/api/app`.
- Files remain on the filesystem under `HOST_DATA_DIR`, while browser access to artifacts goes through authorized backend routes.
- Assistant orchestration and runtime tools run in the single app process.
- Long-running FastSurfer and workspace batch jobs run on the in-process JobWorker (`api_service/jobs/`).
- Case rename/delete and workspace management are handled through the API layer so database rows and filesystem paths stay aligned.

## Manual Verification

Check the Redis sysctl:

```bash
cat /proc/sys/vm/overcommit_memory
```

Expected result:

```text
1
```
