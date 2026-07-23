# NeuroCade monolith image: one process serves the API + built SPA and launches
# analysis tools via Apptainer. Running Apptainer inside Docker requires
# `--privileged --device /dev/fuse` at runtime.

FROM node:22-alpine AS client-build
WORKDIR /app/client
COPY client/package*.json ./
RUN npm ci
COPY client ./
RUN npm run build

# Pinned to bookworm: the Apptainer .deb depends on libfuse3-3, which Debian
# trixie (the current python:3.12-slim) replaced with libfuse3-4.
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/Deep-MI/NeuroCade"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/api-service:/app:/app/packages/neurocade-runtime-tools/src \
    UV_PROJECT_ENVIRONMENT=/opt/neurocade-venv \
    PATH="/opt/neurocade-venv/bin:$PATH"

WORKDIR /app

# Apptainer is the tool runtime. squashfs-tools/fuse2fs/uidmap support its
# rootless SIF execution; the .deb is pulled from the official release.
ARG APPTAINER_VERSION=1.3.6
ARG APPTAINER_SHA256=2723b2928cfc30edf687723c49556ec4e013f0bf7cdb43a5a76bca7bd3c70792
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl uidmap fuse2fs squashfs-tools \
    && curl -fsSL -o /tmp/apptainer.deb \
        "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
    && echo "${APPTAINER_SHA256}  /tmp/apptainer.deb" | sha256sum -c - \
    && apt-get install -y --no-install-recommends /tmp/apptainer.deb \
    && rm -f /tmp/apptainer.deb \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY packages/neurocade-runtime-tools ./packages/neurocade-runtime-tools
RUN pip install --no-cache-dir uv==0.8.17 \
    && uv sync --locked --no-dev --no-editable \
    && pip uninstall -y uv \
    && rm -rf /root/.cache/uv

COPY backend_common ./backend_common
COPY api-service ./api-service
COPY migrations ./migrations
COPY config ./config
COPY scripts/update_checker.py ./scripts/update_checker.py
COPY --from=client-build /app/client/dist ./client/dist

EXPOSE 8000

# IMPORTANT: run a SINGLE uvicorn worker. The in-process JobManager and SQLite's
# single-writer model assume one process; do NOT add --workers / WEB_CONCURRENCY
# (see lifespan() in api_service/main.py).
CMD ["python", "-m", "uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
