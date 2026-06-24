# NeuroCade monolith image: one process that serves the API + built SPA and
# launches analysis tools via Apptainer (see MONOLITH_REFACTOR_PLAN.md §2.3).
#
# Running Apptainer *inside* this container requires a privileged container at
# runtime (compose/run set `privileged: true` + /dev/fuse). The native
# deployment (uv run uvicorn) needs none of that and is the recommended default.
#
# The SPA is built on the host (npm run build) and packaged here, rather than
# built in-image — see scripts/build_image.sh, which does both in one step.

# Pinned to bookworm: the Apptainer .deb depends on libfuse3-3, which Debian
# trixie (the current python:3.12-slim) replaced with libfuse3-4.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/api-service:/app:/app/packages/neurocade-runtime-tools/src

WORKDIR /app

# Apptainer is the tool runtime. squashfs-tools/fuse2fs/uidmap support its
# rootless SIF execution; the .deb is pulled from the official release.
ARG APPTAINER_VERSION=1.3.6
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl uidmap fuse2fs squashfs-tools \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/apptainer.deb \
        "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_${arch}.deb" \
    && apt-get install -y --no-install-recommends /tmp/apptainer.deb \
    && rm -f /tmp/apptainer.deb \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY packages/neurocade-runtime-tools ./packages/neurocade-runtime-tools
RUN pip install --no-cache-dir uv \
    && uv pip install --system -r pyproject.toml

COPY backend_common ./backend_common
COPY api-service ./api-service
COPY migrations ./migrations
COPY config ./config
COPY scripts/update_checker.py ./scripts/update_checker.py
# Built SPA (host-built) served in-process via FastAPI StaticFiles (no gateway).
COPY client/dist ./client/dist

EXPOSE 8000

# Generate the core catalog on every fresh start, then serve the monolith by
# default (so `docker run <image>` just works, no compose).
# IMPORTANT: run a SINGLE uvicorn worker. The in-process JobManager and SQLite's
# single-writer model assume one process; do NOT add --workers / WEB_CONCURRENCY
# (see lifespan() in api_service/main.py).
CMD ["sh", "-c", "python -m neurocade_runtime_tools.docker_catalog && exec python -m uvicorn api_service.main:app --host 0.0.0.0 --port 8000"]
