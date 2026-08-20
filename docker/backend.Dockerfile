# Canonical NeuroCade application image. Tool containers are launched only by
# the host-native authenticated runtime bridge, never from this image.

FROM node:22-alpine AS client-build
WORKDIR /app/client
RUN npm install --global npm@11.10.0
COPY client/package*.json ./
RUN npm ci
COPY client ./
RUN npm run build

FROM python:3.12-slim AS sqlite-build

ARG SQLITE_AUTOCONF_VERSION=3530400
ARG SQLITE_AUTOCONF_SHA3=454e45f61c6bd75b7420e7190732dea03ce6639c63ada47bbc592f67fc340338

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && curl --fail --show-error --location \
      "https://www.sqlite.org/2026/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz" \
      --output /tmp/sqlite.tar.gz \
    && SQLITE_AUTOCONF_SHA3="$SQLITE_AUTOCONF_SHA3" python -c \
      'import hashlib, os, pathlib; archive = pathlib.Path("/tmp/sqlite.tar.gz"); actual = hashlib.sha3_256(archive.read_bytes()).hexdigest(); expected = os.environ["SQLITE_AUTOCONF_SHA3"]; assert actual == expected, f"SQLite SHA3 mismatch: {actual}"' \
    && mkdir /tmp/sqlite \
    && tar -xzf /tmp/sqlite.tar.gz --strip-components=1 -C /tmp/sqlite \
    && cd /tmp/sqlite \
    && ./configure --prefix=/opt/sqlite --disable-static \
    && make -j2 \
    && make install

FROM python:3.12-slim

ARG NEUROCADE_VERSION=0.0.0

LABEL org.opencontainers.image.source="https://github.com/Deep-MI/NeuroCade"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEUROCADE_BUILD_VERSION="$NEUROCADE_VERSION" \
    PYTHONPATH=/app/api-service:/app:/app/packages/neurocade-runtime-tools/src \
    UV_PROJECT_ENVIRONMENT=/opt/neurocade-venv \
    PATH="/opt/neurocade-venv/bin:$PATH"

WORKDIR /app

COPY --from=sqlite-build /opt/sqlite/lib/libsqlite3.so* /usr/local/lib/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && ldconfig \
    && python -c 'import sqlite3; assert tuple(map(int, sqlite3.sqlite_version.split("."))) >= (3, 51, 3), sqlite3.sqlite_version' \
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
