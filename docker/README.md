# Docker Image

`docker/backend.Dockerfile` builds one image, published on release as
`ghcr.io/deep-mi/neurocade`:

1. a Node stage builds the React SPA
2. a Python runtime stage installs backend dependencies and Apptainer
3. uvicorn serves both `/api/app` and the built SPA

Build:

```bash
./scripts/run.sh build
```

Run:

```bash
./scripts/run.sh start -d
```

The container needs `--privileged --device /dev/fuse` so Apptainer can execute
tool images inside Docker. `scripts/run.sh` supplies those flags, mounts
`neurocade-data/` at `/data`, and runs the application with the invoking host
UID/GID. The launcher performs a one-time ownership migration for its writable
data, SIF, cache, and database mounts.

`NEUROCADE_GPU_MODE=auto` probes Docker's NVIDIA passthrough, adds `--gpus all`
when it works, and verifies that CUDA initializes inside the prepared tool
image. Use `cuda` to require both checks and fail early, or `cpu` to skip them.

Run Analysis images are prepared as persistent, architecture-specific SIF files
under `neurocade-data/sif/` during startup. Use
`./scripts/run.sh prepare-tools` to populate them without starting the app, or
set `NEUROCADE_PREPARE_TOOLS=false` to defer conversion until first use.

On Apple Silicon, the installer selects `linux/amd64` for the outer Docker
image so the amd64 NeuroDesk tools and Apptainer runtime execute consistently
under Docker Desktop emulation. Enable Docker Desktop Rosetta support before
installing; this mode is slower and does not support GPU execution.

Workflow definitions are loaded from `config/neuroimaging_tools.yaml` by
`tool_search` and `tool_call`.
