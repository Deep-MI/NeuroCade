FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/api-service:/app:/app/packages/neurocade-runtime-tools/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates curl \
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

EXPOSE 8000 58081
