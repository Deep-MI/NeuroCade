# Installation

NeuroCade now installs as one Docker container. Docker is the only runtime
dependency for the default Linux/local install path.

## Quick Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --mode local
```

From an existing checkout:

```bash
./scripts/install.sh --mode local
```

The installer:

- writes `.env`
- creates `neurocade-data/`
- downloads the curated sample case into `sample_case/` when it is missing
- pulls the published NeuroCade image from GHCR
- starts one container with `scripts/run.sh`

No Docker Compose, host Python virtualenv, or host Node.js install is required.

## Runtime Commands

```bash
./scripts/run.sh start -d       # pull if needed and start in the background
./scripts/run.sh pull           # update the configured published image
./scripts/run.sh start --build -d # build the current checkout for development
./scripts/run.sh status
./scripts/run.sh logs
./scripts/run.sh stop
./scripts/run.sh build
```

The app is available at `http://localhost:8000` by default.

`scripts/run.sh` also keeps the local sample source available. If
`sample_case/FastSurfer_Rhineland_0000` is missing, it uses the NeuroCade Docker
image to download the release artifact before starting the app. Set
`NEUROCADE_SKIP_SAMPLE_CASE=true` to skip this step.
The sample archive is pinned independently of app releases and verified against
`NEUROCADE_SAMPLE_CASE_SHA256`.

## Configuration

The installer writes `.env`. The most important values are:

```bash
APP_BASE_URL=http://localhost:8000
APP_HTTP_BIND=127.0.0.1
APP_HTTP_PORT=8000
NEUROCADE_IMAGE=ghcr.io/deep-mi/neurocade:latest
HOST_DATA_DIR=/path/to/NeuroCade/neurocade-data
NEUROCADE_DB_DIR=/path/on/local-disk/neurocade-db
DATABASE_URL=sqlite+pysqlite:////path/on/local-disk/neurocade-db/neurocade.db
NEUROCADE_GPU_MODE=auto
```

Use `ghcr.io/deep-mi/neurocade:beta` for the current prerelease channel, or an
exact release tag such as `ghcr.io/deep-mi/neurocade:v2026.7.23-beta.1` for a
reproducible deployment.

```bash
./scripts/install.sh --mode local --image ghcr.io/deep-mi/neurocade:v2026.7.23-beta.1
```

`DATABASE_URL` is the host-side path used by local/admin tooling. The launcher
mounts `NEUROCADE_DB_DIR` separately at `/database` and uses
`/database/neurocade.db` inside Docker. Keep this directory on a local
filesystem; SQLite WAL is not suitable for NFS or other network filesystems.
Large imaging inputs and outputs can remain under `HOST_DATA_DIR`.

### Pre-beta database reset

This beta uses a clean database baseline and does not upgrade databases created
by earlier development builds. Before starting it against an existing pre-beta
installation, reset the local application state from the repository root:

```bash
./scripts/admin/reset_app_state.sh --yes
```

This removes the SQLite database and workspace data under `HOST_DATA_DIR`; copy
research data elsewhere first if it must be retained. The reset preserves
`license.txt`.

## Apple Silicon

Apple Silicon Macs run the NeuroDesk runtime containers through amd64 emulation.
The installer writes `NEUROCADE_DOCKER_PLATFORM=linux/amd64` automatically on
Darwin arm64 hosts; Docker Desktop must have Rosetta support enabled. Analysis
is slower than on native Linux amd64, and GPU execution is not supported on
macOS. Override the setting only when you have an alternative compatible
runtime:

```bash
NEUROCADE_DOCKER_PLATFORM=linux/amd64
```

## Tool Runtime

The app launches neuroimaging tools with Apptainer inside the Docker container.
`scripts/run.sh` supplies the required FUSE privileges and runs the application
as the invoking host UID/GID, so newly created database, cache, and analysis
files remain writable by the host user. Set `NEUROCADE_UID` and
`NEUROCADE_GID` only when the mounted data should belong to a different user.

GPU-capable workflows use `NEUROCADE_GPU_MODE`:

- `auto` (default) verifies both Docker GPU passthrough and CUDA initialization
  inside the selected tool image; it selects CPU if either check fails.
- `cuda` requires working NVIDIA passthrough and a CUDA-enabled tool image, and
  stops startup or run submission when either requirement is unavailable.
- `cpu` skips the NVIDIA probe and runs workflows on the CPU.

On CUDA systems the launcher adds `--gpus all`. It also uses:

```bash
--privileged --device /dev/fuse
```

Run Analysis workflows and assistant tool metadata are defined in
`config/neuroimaging_tools.yaml` and loaded by `tool_search` and `tool_call`.
Unknown catalog fields are
rejected at startup so misspelled settings cannot silently fall back to defaults.
Workflow terminal logs are retained per run under each case's
`scripts/runs/<run-id>/` directory.

Startup prepares the pinned FastSurfer and dcm2niix images as verified,
architecture-specific SIF files in `neurocade-data/sif/`. Downloads run in
parallel, resume when supported, and are reused after checksum validation.
Prepare them independently with `./scripts/run.sh prepare-tools`. Run
`./scripts/run.sh doctor` to validate Docker, FUSE, storage, images, GPU, and LLM
configuration.

## LLM Providers

Supported installer choices:

- `openai-compatible`
- `anthropic`
- `google`
- `ollama`
- `no-llm`

Unattended installation (`--yes` or redirected input) defaults to `no-llm`
unless `--llm-provider` is supplied explicitly.

For host-local Ollama, use `http://host.docker.internal:11434`; the run script
adds the Docker host gateway mapping.
