# Installation

NeuroCade uses a host-native runtime bridge and requires one matched profile:

- `docker`: the application is a Docker container and tools run as direct host Docker containers.
- `apptainer`: the application is a published SIF and tools run as verified SIFs with rootless Apptainer (Linux amd64).

Mixed profiles, nested container runtimes, and legacy runtime settings are not supported.

## Install

Docker (Linux or macOS):

```bash
./scripts/install.sh --mode local
```

The runtime is optional. macOS selects Docker. Linux selects rootless Apptainer
when it passes the host capability checks and otherwise falls back to Docker.
An existing `NEUROCADE_RUNTIME` setting is preserved on reinstall; pass
`--runtime docker|apptainer` to override it.

Rootless Apptainer (Linux amd64):

```bash
./scripts/install.sh --runtime apptainer --mode local \
  --app-sif-url https://github.com/Deep-MI/NeuroCade/releases/download/VERSION/neurocade-app-VERSION-amd64.sif \
  --app-sif-sha256 SHA256
```

The installer pins `uv`, installs managed Python 3.12, creates
`.runtime/bridge-venv`, generates `.runtime/bridge-token` with mode `0600`,
writes a fresh `.env`, prepares the default tool images, and starts the matched
application and bridge. Rerun the installer to migrate an older installation;
existing data, SQLite state, outputs, uploads, image caches, and `license.txt`
are preserved.

The managed `uv` executable and Python installation live under `.runtime` and
are used directly by the launcher. They do not need to be added to `PATH` and
do not conflict with another `uv` installation. On Apple Silicon, the installer
uses native arm64 tools even when it was started from a Rosetta-translated shell.

Interactive installs prompt for provider settings. `--yes` deliberately skips
all prompts, preserves configured values, and accepts defaults.

## Commands

```bash
./scripts/run.sh start -d
./scripts/run.sh stop
./scripts/run.sh status
./scripts/run.sh logs
./scripts/run.sh pull
./scripts/run.sh build          # canonical Docker application image
./scripts/run.sh prepare-tools
./scripts/run.sh doctor
```

Startup validates the host, updates the managed bridge, prepares the application
artifact and pinned tools, verifies bridge protocol/backend compatibility, and
then starts the application. The app is available at `http://localhost:8000` by
default. The Docker profile maps `host.docker.internal` through Linux's
host-gateway; the Apptainer profile uses host networking and binds the bridge to
loopback only.

## Configuration

The runtime contract is explicit:

```bash
NEUROCADE_RUNTIME=docker
NEUROCADE_BRIDGE_URL=http://127.0.0.1:8765
NEUROCADE_BRIDGE_TOKEN_FILE=/path/to/NeuroCade/.runtime/bridge-token
NEUROCADE_BRIDGE_PORT=8765
HOST_DATA_DIR=/path/to/NeuroCade/neurocade-data
NEUROCADE_DATABASE_VOLUME=neurocade-database
NEUROCADE_GPU_MODE=auto
```

For Apptainer, also set the published `NEUROCADE_APP_SIF_URL` and
`NEUROCADE_APP_SIF_SHA256`. Tool OCI digests and SIF checksums/URLs are in
`config/tool_images.json`.

`NEUROCADE_GPU_MODE=auto` selects CUDA only after the bridge validates the host
and selected tool image. `cuda` requires it; `cpu` disables it. Neither profile
uses sudo, fakeroot, privileged containers, writable SIFs, FUSE passthrough, or
a Docker socket inside the NeuroCade application.

Docker keeps SQLite in the native Linux `NEUROCADE_DATABASE_VOLUME`; only case
files and outputs use the host bind mount. Apptainer keeps SQLite under
`.runtime/database` on its native Linux host.
Large inputs and outputs may remain under `HOST_DATA_DIR`. Use
`./scripts/admin/reset_app_state.sh --yes` for a local reset; it preserves
`license.txt` and the managed bridge/image installation.
