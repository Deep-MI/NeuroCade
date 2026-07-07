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
- copies a FreeSurfer license to `neurocade-data/license.txt` when provided
- downloads the curated sample case into `sample_case/` when it is missing
- builds the single Docker image
- starts one container with `scripts/run.sh`

No Docker Compose, host Python virtualenv, or host Node.js install is required.

## Runtime Commands

```bash
./scripts/run.sh start -d       # build if needed and start in the background
./scripts/run.sh start --build -d
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

## Configuration

The installer writes `.env`. The most important values are:

```bash
APP_BASE_URL=http://localhost:8000
APP_HTTP_BIND=127.0.0.1
APP_HTTP_PORT=8000
NEUROCADE_HOST_DATA_DIR=/path/to/NeuroCade/neurocade-data
DATABASE_URL=sqlite+pysqlite:////path/to/NeuroCade/neurocade-data/neurocade.db
NEUROCADE_CONTAINER_DATABASE_URL=sqlite+pysqlite:////data/neurocade.db
NEUROCADE_RUNTIME_BACKEND=apptainer
FREESURFER_LICENSE=/path/to/NeuroCade/neurocade-data/license.txt
```

`DATABASE_URL` is the host-side path used by local/admin tooling.
`NEUROCADE_CONTAINER_DATABASE_URL` is the same SQLite database through the
container mount at `/data/neurocade.db`.

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

## FreeSurfer License

FastSurfer and FreeSurfer workflows need a FreeSurfer license. Register at:

https://surfer.nmr.mgh.harvard.edu/registration.html

Then either rerun the installer with the license path, set `FREESURFER_LICENSE`,
or place the file at:

```text
neurocade-data/license.txt
```
