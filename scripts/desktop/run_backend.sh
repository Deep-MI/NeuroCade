#!/usr/bin/env bash
# Launch the NeuroCade monolith backend natively for the desktop app.
# Serves the API + built SPA from one uvicorn process; tools run via the
# configured runtime backend (Apptainer by default, Docker for dev).
# Keep this a SINGLE uvicorn worker: the in-process JobManager and SQLite's
# single-writer model assume one process (do not add --workers).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# Load .env (without overriding values already in the environment).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export PYTHONPATH="api-service:backend_common:.:packages/neurocade-runtime-tools/src"
export HOST_DATA_DIR="${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
: "${DATABASE_URL:=sqlite+pysqlite:///$HOST_DATA_DIR/neurocade.db}"
export DATABASE_URL
export APP_HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
export APP_HTTP_PORT="${APP_HTTP_PORT:-8000}"

if [[ "$APP_HTTP_BIND" == "0.0.0.0" ]]; then
  APP_DISPLAY_HOST="localhost"
else
  APP_DISPLAY_HOST="$APP_HTTP_BIND"
fi
echo "Starting NeuroCade at http://${APP_DISPLAY_HOST}:${APP_HTTP_PORT}/"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v uv >/dev/null 2>&1 && echo uv || true)"
  if [[ "$PYTHON_BIN" == "uv" ]]; then
    exec uv run uvicorn api_service.main:app --host "$APP_HTTP_BIND" --port "$APP_HTTP_PORT"
  fi
  PYTHON_BIN="$(command -v python3)"
fi

exec "$PYTHON_BIN" -m uvicorn api_service.main:app --host "$APP_HTTP_BIND" --port "$APP_HTTP_PORT"
