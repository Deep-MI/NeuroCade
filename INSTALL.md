# Installation

NeuroCade uses a host-native runtime bridge and requires one matched profile:

- `docker`: the application is a Docker container and tools run as direct host Docker containers.
- `apptainer`: the application is a verified release SIF, or a SIF converted from the local source build, and tools run as verified SIFs with rootless Apptainer (Linux amd64).

Mixed profiles, nested container runtimes, and legacy runtime settings are not supported.

## Install

Download the latest stable installer (Python 3 and curl are required):

```bash
curl -fL --retry 3 -o neurocade-install.sh https://github.com/Deep-MI/NeuroCade/releases/latest/download/neurocade-install.sh && bash neurocade-install.sh --mode local
```

Each release publishes `neurocade-install.sh`, its `.sha256` checksum, and an
installer entry in `neurocade-release.json`. A version-specific URL is
`https://github.com/Deep-MI/NeuroCade/releases/download/<tag>/neurocade-install.sh`.
The downloaded installer defaults to that exact release. All source and runtime
artifacts are resolved from that release; stable never falls back to beta.

```bash
bash neurocade-install.sh --version beta --runtime docker
bash neurocade-install.sh --version v2026.9.9-beta.1 --runtime apptainer
```

Docker builds the verified source archive and uses the bridge from that same
source revision. Apptainer uses its matching SIF and bridge wheel. Runtime
requirements are macOS with Docker, or Linux amd64 with Docker/rootless Apptainer.
When both runtimes are available, the interactive installer offers a menu with
Apptainer recommended on Linux. Start the Docker engine before setup.

For source development, choose the checkout explicitly:

```bash
./scripts/install.sh --runtime docker --mode local
./scripts/install.sh --runtime apptainer --mode local --build-from-source
```

A checkout cannot switch source revisions through `--version`; use the standalone
installer in a separate empty directory. The Apptainer source-build path requires
Docker. A separately selected Docker `--image` remains an advanced checkout-only
option; it is not accepted for verified archive installs.

## Setup choices and automation

Menus support arrow keys/Enter and numbered input; basic terminals use numbered
input. `q` or Ctrl-C cancels. Explicit flags override detected/default choices.
Existing installations offer update while retaining configuration, reconfiguration
of the installed version, or cancellation. Run `scripts/install.sh --reconfigure`
to review existing settings directly. Existing archive updates retain the runtime;
switching database backends requires an explicit migration.

The provider menu includes “Configure later,” OpenAI-compatible endpoints,
Anthropic, Google, and Ollama. Model discovery queries the selected endpoint with
a bounded timeout, displays up to 50 model IDs, and always offers manual entry.
An unavailable endpoint does not prevent manual configuration. Existing model
names remain valid choices; discovery does not prove model suitability or access.

`--yes` suppresses every prompt, including the directory prompt. Missing mandatory
settings fail before downloading toolchains/images. A fresh unattended install
uses no assistant unless `--llm-provider` or provider configuration selects one.
Set credentials and the matching model variable (`LLM_BACKEND_MODEL`,
`ANTHROPIC_MODEL`, `GOOGLE_MODEL`, or `OLLAMA_MODEL`) in the environment or `.env`.
Use `NEUROCADE_INSTALL_DIR` to choose the destination without a prompt.

Setup checks runtime access, supported architecture, writable storage, and at
least 8 GiB free for setup before downloading toolchains/images. This is a setup
floor, not a guarantee that all workflows fit. Docker Desktop's VM disk capacity
and additional workflow images may require more storage.

## Errors and recovery

Failures identify the installation stage and next action. Build/start diagnostics
are streamed to the terminal and `.runtime/install.log` with configured secrets,
authorization headers, and URL credentials redacted. Logs have owner-only access.
Configuration is written atomically and its original copy is preserved in
`.runtime/preinstall-env`.

Failed or interrupted release downloads never become accepted archives. Rerun
the same command after resolving connectivity, permissions, or disk issues.
An interrupted first installation resumes its extracted version, preserving data;
it does not enter the transactional upgrade path until setup has completed.
A checksum mismatch stops installation; retry from the official release rather
than bypassing verification. Missing stable assets require a valid stable release
or an explicit `--version beta` choice.

The installer manages uv and Python under `.runtime`, keeping them separate from
other installations, and records release provenance after successful setup.

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
version. Rollback material is kept under `.runtime/update-transaction` while
the application is stopped, so an abrupt host interruption does not discard it.
If automatic rollback cannot complete, the previous app is not restarted and
the updater prints the retained recovery path for manual repair.

The same path repairs older archive installations when the remote installer is
run again. Git checkouts are never overwritten; update them with Git and rerun
`scripts/install.sh`. Update mode rejects forwarded installer options such as a
runtime change or `--no-start`; a successful update always includes a verified
healthy start. Updates remain an explicit command and are not started by the
web UI or the read-only update checker.

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
