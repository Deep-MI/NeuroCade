# Dockerization

NeuroCade is a **single-process monolith** (see `MONOLITH_REFACTOR_PLAN.md`). One
FastAPI/uvicorn process serves the API and the built SPA, runs background jobs
in-process, and launches analysis tools via Apptainer. There is no separate
gateway, worker, runtime-runner, Postgres, or Redis.

## Image

`docker/backend.Dockerfile` is a two-stage build:

1. **`client-build`** (`node:22-alpine`) builds the React SPA (`client/dist`).
2. **Runtime** (`python:3.12-slim-bookworm`) installs the Python deps from
   `pyproject.toml` (via `uv`), bakes in **Apptainer** (official `.deb`; bookworm
   is required for `libfuse3-3`), copies the app + built SPA, and runs uvicorn by
   default (`CMD`).

> The SPA can also be built on the host (`npm --prefix client run build`) and
> copied in — `scripts/build_image.sh` does host build + `docker build`, which
> avoids npm running inside Docker.

## Running

You do **not** need Docker Compose to run one container:

```bash
# plain docker run (see scripts/run_container.sh for a wrapper)
docker run --rm --privileged --device /dev/fuse \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/neurocade-data:/data" \
  --env-file .env \
  neurocade:local
```

`compose.yaml` is kept as a convenience/lifecycle entry point (one `app` service
with `restart`, healthcheck, logs, status) used by the installer and admin
scripts. The native path (`scripts/desktop/run_backend.sh`) needs no container at
all and is the recommended default.

### Apptainer-in-container (the macOS path)

Apptainer is Linux-only, so on macOS it runs **inside** the Linux container. That
requires an elevated container — `--privileged --device /dev/fuse` (Neurodesk
precedent). Set `NEUROCADE_RUNTIME_BACKEND=apptainer` (default). For host-native
development without Apptainer, set `NEUROCADE_RUNTIME_BACKEND=docker` to launch
tools through the host Docker daemon instead.

## Runtime tool execution

- A backend-agnostic `RuntimeContainerRunRequest` is turned into `argv` by the
  selected backend in `neurocade_runtime_tools/runtime_backends.py`
  (`ApptainerBackend` / `DockerBackend`) and run as a local subprocess — no
  socket-mounting sidecar.
- Tool images: a prebuilt, arch-matched SIF when available (`NEUROCADE_SIF_DIR`),
  otherwise `apptainer pull docker://…` from the spec's `docker_uri`.
- Pinned core images: FastSurfer `vnmd/fastsurfer_2.4.2:20260115`, dcm2niix
  `vnmd/dcm2niix_v1.0.20240202:20260512`, FreeSurfer `vnmd/freesurfer_8.1.0:20260311`,
  and the managed bash runtime image.

## Data and config

- `HOST_DATA_DIR=/data` inside the container; bind-mount `neurocade-data/`.
- `DATABASE_URL` defaults to a SQLite file under the data dir (WAL mode).
- External LLM/Ollama endpoints stay env-configured; host-local endpoints use
  `host.docker.internal` (Linux `extra_hosts: host-gateway`).

## Verifying the image

```bash
scripts/build_image.sh                                   # host build + docker build
docker run --rm --privileged --device /dev/fuse neurocade:local apptainer --version
docker run --rm --privileged --device /dev/fuse neurocade:local \
  apptainer exec docker://busybox echo ok                # apptainer runs inside
docker compose config                                    # compose still valid
```
