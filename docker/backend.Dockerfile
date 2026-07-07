# NeuroCade monolith image: one process serves the API + built SPA and launches
# analysis tools via Apptainer. Running Apptainer inside Docker requires
# `--privileged --device /dev/fuse` at runtime.

FROM node:22-alpine AS client-build
WORKDIR /app/client
COPY client/package*.json ./
RUN npm ci
COPY client ./
ARG NC_VITE_API_URL=/api/app
ARG NC_LOCAL_LOGIN=true
ARG NC_CLERK_PUBLIC=
ARG NC_CLERK_TEMPLATE=
RUN VITE_API_URL="${NC_VITE_API_URL}" \
    VITE_LOCAL_AUTH_ENABLED="${NC_LOCAL_LOGIN}" \
    VITE_CLERK_PUBLISHABLE_KEY="${NC_CLERK_PUBLIC}" \
    VITE_CLERK_JWT_TEMPLATE="${NC_CLERK_TEMPLATE}" \
    npm run build

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
COPY --from=client-build /app/client/dist ./client/dist

EXPOSE 8000

# IMPORTANT: run a SINGLE uvicorn worker. The in-process JobManager and SQLite's
# single-writer model assume one process; do NOT add --workers / WEB_CONCURRENCY
# (see lifespan() in api_service/main.py).
CMD ["python", "-m", "uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
