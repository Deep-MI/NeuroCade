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

The desktop launcher starts the local Apptainer backend when needed, waits for the
API to become healthy, opens the app window, and stops the stack on quit if it
started the stack itself. Apptainer remains required, but backend startup is hidden
behind the desktop app after setup. On Linux, the launcher starts Electron with
Chromium sandbox fallback flags used by Electron apps on hosts where the setuid
sandbox is unavailable. Use `--no-desktop` to keep the browser-only local flow.

Useful non-interactive examples:

```bash
./scripts/install.sh --doctor --mode local --llm-provider ollama --yes
./scripts/install.sh --dry-run --mode local --llm-provider ollama --yes
./scripts/install.sh --mode local --llm-provider ollama --no-start --yes
./scripts/install.sh --mode internal --llm-provider openai-compatible
./scripts/install.sh --mode demo --llm-provider openai-compatible
./scripts/install.sh --mode local --llm-provider ollama --with-demo-case
./scripts/install.sh --mode local --no-desktop
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --prerelease --mode local
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --dev --mode local
```

Windows users should run the installer inside a Linux/WSL2 environment with
Apptainer available. Native PowerShell installation is not supported in this
repository yet.

## Prerequisites

The installer checks prerequisites, installs `uv` with confirmation when it is
missing, creates `.venv` from the Python runtime declared in `pyproject.toml`,
can prepare Apptainer/Lima without sudo where possible, and still avoids
changing system package managers unless a missing runtime is required. Install
any remaining missing prerequisites with your site-approved method, then rerun
it.

Main dependencies are:
- uv for the project Python runtime. The installer uses it to create `.venv`
  and install backend/runtime tooling consistently.
- Apptainer. On Linux, the installer can prepare a repo-local unprivileged
  Apptainer runtime when the supporting host tools are present. On macOS,
  Apptainer runs through Lima because it needs a Linux kernel; the installer can
  use an existing Lima install, install Lima through Homebrew when available, or
  download Lima's official binary archive into the repo without Homebrew.
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
newer version is available, the message is written to
`.runtime/logs/update-checker.log` and appears in `./scripts/apptainer/logs.sh`.
If the endpoint is unreachable or returns invalid data, nothing is logged.

## Required Repo Configuration

Copy or edit `.env` before startup. The minimum configuration keys are:

- `HOST_DATA_DIR`: absolute host path to the NeuroCade runtime data directory. Local installs default to `neurocade-data/`.
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
- `NEUROCADE_CONTAINER_ROOT`: optional override for managed runtime containers. Defaults to `.apptainer/containers`.
- `NEUROCADE_INSTALLED_TOOLS_JSONL`: optional override for the generated installed-tool index. Defaults to `llm-data/tool-catalog/installed_tools.jsonl`.
- `OLLAMA_MODEL` if you want the optional local Gemma 4 backend

Example:

```bash
HOST_DATA_DIR=/abs/path/to/neurocade-data
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

Start the app with the standard Apptainer launcher:

```bash
./scripts/apptainer/up.sh -d
```

Notes:

- `gemma4:e2b` is the safest starting point on the current 16 GB GPU.
- `gemma4:e4b` may also fit, but with less headroom.
- Local Ollama support expects an Ollama service reachable at `OLLAMA_BASE_URL`; the rootless launcher does not require Docker volumes.

## Runtime Containers And Tool Index

The assistant uses the containers installed under `NEUROCADE_CONTAINER_ROOT` and a generated installed-tool index. The installer runs:

```bash
./scripts/containers.sh prefetch core
./scripts/containers.sh install core --source auto
```

The prefetch step downloads direct image artifacts in the background while other
installer work continues. It fetches FastSurfer, dcm2niix, the managed bash
runtime, and the FreeSurfer image when they are missing. The managed bash/Python
runtime is published as `bash-image-python-3.12.sif` on each GitHub release.
The Postgres, Redis, and Traefik service images are also published as release
SIF artifacts (`postgres-16-alpine.sif`, `redis-7-alpine.sif`, and
`traefik-v2.11.14.sif`). Redis is built from the pinned
`docker://redis:7.2.4-alpine` source. Tagged installs download release assets
from their matching release; dev installs use the latest release assets and fall
back to OCI pulls if a new release asset is not available yet. FreeSurfer image
bytes can be downloaded without a license, but FreeSurfer is only indexed and
exposed as an installed runtime tool when a FreeSurfer license is available.

Known core containers use prebuilt tool indexes shipped with the runtime-tools
package. This avoids install-time command discovery and help harvesting for the
standard images. The FreeSurfer prebuilt index is intentionally curated instead
of indexing every executable in the image.

You can refresh the index without reinstalling containers:

```bash
./scripts/containers.sh refresh-index
```

Maintainers can force live command discovery for validation or index refreshes:

```bash
./scripts/containers.sh refresh-index --rebuild-index
./scripts/containers.sh install core --rebuild-index
```

Check installed/missing containers and index freshness with:

```bash
./scripts/containers.sh status --json
```

With `--source auto`, managed runtime containers try direct image downloads first,
then fall back to upstream Docker pulls or local Buildfile builds when direct
images are unavailable. On macOS/Lima, local Buildfile fallback is disabled by
default because it can require large VM disk space; publish the GitHub release
asset or set `NEUROCADE_ALLOW_LOCAL_CONTAINER_BUILDS=1` to attempt it explicitly.
FreeSurfer is installed from NeuroContainers only when a FreeSurfer license is
found.

You can also install additional NeuroContainers by app or repository name. The container helper searches the Docker Hub `vnmd` namespace, picks the best matching repository and latest build tag, pulls it with Apptainer, then indexes the installed command:

```bash
./scripts/containers.sh search matlab
./scripts/containers.sh install matlab
```

## Sample Case Setup

The seeded app sample case is generated from a real FastSurfer run on the Rhineland T1-weighted example scan and kept entirely under [sample_case](sample_case).
Release builds attach the curated seed as
`neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz` when
`sample_case/FastSurfer_Rhineland_0000` is present. The installer downloads and
extracts that release asset when `--with-demo-case` is used. If the chosen
release does not include the sample-case archive, the installer scans older
releases in the selected channel for that archive only; other release assets
still come from the chosen release. It falls back to building the sample locally
only if no matching release asset is available and a FreeSurfer license is
configured. Maintainers should generate or refresh that directory before cutting
a release, or set `BUILD_SAMPLE_CASE_ARTIFACT=true` in a release environment
that has the raw sample data, FastSurfer container, and FreeSurfer license.

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

Use the rootless repo wrapper:

```bash
./scripts/apptainer/up.sh -d
```

This script:

- fetches the required infrastructure SIFs with `scripts/apptainer/images.sh infra`
- refreshes the installed-container tool index with `scripts/containers.sh refresh-index`
- starts Postgres, Redis, Traefik, the Python services, and the configured frontend server as user-owned processes
- binds FastSurfer/FreeSurfer tool execution through Apptainer without Docker or sudo
- writes logs and pid files under `.runtime/`

The application is available at:

- `http://localhost:8005`
- `http://localhost:8005/workspaces/personal-workspace/cases` for the default local workspace

For local desktop installs, users normally do not need these URLs directly; the
Electron launcher loads the local app automatically.

Traefik dashboard:

- `http://localhost:8080`

`CLIENT_SERVE_MODE=static` builds `client/dist` when needed and serves the
static client behind Traefik with SPA fallback. `CLIENT_SERVE_MODE=vite` starts
the Vite development server and is intended for active source development.
Stable local, internal, and demo installs should use `static`.

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
./scripts/apptainer/images.sh preflight
./scripts/apptainer/status.sh
./scripts/apptainer/logs.sh -f
./scripts/apptainer/down.sh
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
runtime index. The generated files are local runtime data and are not committed:

- `llm-data/tool-catalog/installed_containers.json`
- `llm-data/tool-catalog/installed_tools.jsonl`

Refresh them after container installs or removals:

```bash
./scripts/containers.sh refresh-index
```

## Application Services

The local stack runs these core services:

- `postgres`: application metadata, authorization, workflow/thread mapping
- `api-service`: browser-facing API boundary under `/api/app`, plus in-process assistant/runtime orchestration under `api_service/assistant/` and `api_service/runtime/`
- `api-worker`: Celery worker for workspace batch and FastSurfer queues
- `backend_common`: shared settings, auth, database, provider, storage, and sample-seeding utilities
- `packages/neurocade-runtime-tools`: local runtime container management, installed-tool index generation, and Apptainer routing package
- `migrations`: Alembic migration history used during database bootstrap

Additional environment variables:

```bash
POSTGRES_USER=neurocade_user
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_DB=neurocade_db
DEPLOYMENT_PROFILE=local
CLIENT_SERVE_MODE=static
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
blocked. `CLIENT_SERVE_MODE=static` serves the built frontend and
`CLIENT_SERVE_MODE=vite` runs the Vite development server. `internal` and
`demo` deployments must use `LOCAL_AUTH_ENABLED=false` and a complete Clerk
configuration.

## Runtime Architecture

- Browser-facing API routes are served under `/api/app`.
- Files remain on the filesystem under `HOST_DATA_DIR`, while browser access to artifacts goes through authorized backend routes.
- Assistant orchestration and runtime tools run inside `api-service`.
- Long-running FastSurfer and workspace batch jobs run through `api-worker` queues backed by Redis.
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
