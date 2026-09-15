# Installation

NeuroCade uses a host-native runtime bridge and requires one matched profile:

- `docker`: the application is a Docker container and tools run as direct host Docker containers.
- `apptainer`: the application is a verified release SIF, or a SIF converted from the local source build, and tools run as verified SIFs with rootless Apptainer (Linux amd64).

Mixed profiles, nested container runtimes, and legacy runtime settings are not supported.

## Install

Automatic install:

```bash
./scripts/install.sh --mode local
```

Fresh Linux installs prefer rootless Apptainer and automatically download the
latest compatible stable NeuroCade release, its checksum, and its matching host
bridge. If no compatible stable release exists, the installer falls back to the
newest compatible release and reports the selected pre-release. Linux uses
Docker when rootless Apptainer is unavailable; macOS uses Docker.
An existing `NEUROCADE_RUNTIME` setting is preserved on reinstall; pass
`--runtime docker|apptainer` to override it.

Install a published beta explicitly:

```bash
# Docker rolling beta image
./scripts/install.sh --mode local --runtime docker --image docker.io/deepmi/neurocade:beta

# Latest Apptainer beta release
./scripts/install.sh --mode local --runtime apptainer --version beta
```

For a reproducible install, use a versioned Docker Hub image with `--image`, or
an exact v-prefixed GitHub release tag with Apptainer, for example
`--version v2026.9.9-beta.1`. The installer rejects `--image` for Apptainer and
`--version` for Docker so a requested version cannot be silently ignored.

Build an Apptainer installation from the current checkout:

```bash
./scripts/install.sh --runtime apptainer --mode local --build-from-source
```

This special path requires Docker. It builds the canonical Linux/amd64 OCI
image from local files, converts it to a SIF with Apptainer, and installs the
host bridge from the same checkout. Without Docker, only the release-download
Apptainer path is supported.

The installer pins `uv`, installs managed Python 3.12, creates
`.runtime/bridge-venv`, generates `.runtime/bridge-token` with mode `0600`,
writes a fresh `.env`, prepares the default tool images, records the installed
release provenance, and starts the matched application and bridge. Rerunning
the remote installer upgrades an installer-owned archive installation through
the transactional update path described below.

Docker installs build the application from the current checkout by default, so
the application and host bridge always share one protocol revision. Pass
`--image docker.io/deepmi/neurocade:<tag>` to opt into a prebuilt image.
Apptainer release artifacts and checksums are discovered automatically.

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
./scripts/run.sh pull           # Docker pull; Apptainer artifacts are installer-managed
./scripts/run.sh build          # canonical Docker application image
./scripts/run.sh prepare-tools
./scripts/run.sh doctor
```

Startup validates the host, updates the managed bridge, prepares the application
artifact and policy-managed tools, verifies bridge protocol/backend compatibility, and
then starts the application. The app is available at `http://localhost:8000` by
default. The Docker profile maps `host.docker.internal` through Linux's
host-gateway; the Apptainer profile uses host networking and binds the bridge to
loopback only.

## Updates

Installer-owned archive installations can check or apply published releases:

```bash
./scripts/update.sh --check
./scripts/update.sh --yes
./scripts/update.sh --channel beta --yes
./scripts/update.sh --version v2026.9.9 --yes
```

Each release includes a source archive and checksum in the GitHub release
manifest. The updater downloads and verifies them before stopping NeuroCade,
refuses to continue while a workflow is active or managed source files were
edited, and preserves `.env`, runtime state, cases, outputs, uploads, and
`license.txt`. It then stops the app, backs up SQLite, switches the managed
source, installs the matching runtime artifacts, and waits for a healthy start.
If installation or startup fails, it restores the prior source, database,
runtime artifact, configuration, and Docker image tag, then restarts the prior
version.

The same path repairs older archive installations when the remote installer is
run again. Git checkouts are never overwritten; update them with Git and rerun
`scripts/install.sh`. Updates remain an explicit command and are not started by
the web UI or the read-only update checker.

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

Apptainer release selection is installer-managed. Use `scripts/update.sh` to
update its selected channel or exact tag. Tool image
policies, OCI digests, and SIF checksums/URLs are in `config/tool_images.json`.
Most tools are immutable. Neurodesk tools may instead use a version-scoped
repository's `latest` tag, allowing image rebuilds without changing the bundled
tool version.

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

## Hardware and storage

The current FastSurfer and DICOM conversion tool artifacts total about 1.9 GB
to download. Allow several additional GB for the application runtime,
container extraction/cache, MRI inputs, and generated outputs. FastSurfer CPU
processing requires at least 8 GB RAM; more memory and storage are advisable for
multiple or high-resolution cases. A supported GPU is optional.

## Uninstall

```bash
./scripts/uninstall.sh --yes
```

The uninstaller removes only resources carrying this installation's ownership
record. Cases and databases are retained by default; use `--purge-data` to
remove them when their ownership can be proven. Use `--remove-images` to remove
a locally built application image when it has the matching ownership label.
The checkout is always preserved because it may contain user changes. Shared or
unrecognized containers, volumes, images, data directories, and host-installed
dependencies are also preserved. Apptainer's shared user cache is outside the
installation and is never removed; inspect it with `apptainer cache list` and,
when you are certain its shared contents are no longer needed, reclaim it with
`apptainer cache clean`.
