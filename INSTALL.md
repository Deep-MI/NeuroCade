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
NEUROCADE_RUNTIME_BACKEND=apptainer
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

The app launches neuroimaging tools with Apptainer inside the Docker container,
so `scripts/run.sh` uses:

```bash
--privileged --device /dev/fuse
```

Runtime tools are listed in `config/runtime_tools.json`. `tool_search` searches
that configured list only, and `tool_call` requires the configured `container_id`
and `tool_id` returned by search. Startup no longer generates or refreshes a
tool catalog.

Apptainer resolves tool images from `NEUROCADE_SIF_DIR` when a matching SIF is
present, otherwise it falls back to `docker://...` and uses its cache under the
mounted data directory.

## LLM Providers

Supported installer choices:

- `openai-compatible`
- `anthropic`
- `google`
- `ollama`
- `no-llm`

For host-local Ollama, use `http://host.docker.internal:11434`; the run script
adds the Docker host gateway mapping.
