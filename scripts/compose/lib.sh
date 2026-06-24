#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

export NEUROCADE_HOST_DATA_DIR="${NEUROCADE_HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
export HOST_DATA_DIR="/data"
export NEUROCADE_SIF_DIR="${NEUROCADE_SIF_DIR:-$NEUROCADE_HOST_DATA_DIR/sif}"
if [[ -z "${NEUROCADE_CONTAINER_DATABASE_URL:-}" ]]; then
  if [[ -z "${DATABASE_URL:-}" || "$DATABASE_URL" == sqlite:* ]]; then
    export NEUROCADE_CONTAINER_DATABASE_URL="sqlite+pysqlite:////data/neurocade.db"
  else
    export NEUROCADE_CONTAINER_DATABASE_URL="$DATABASE_URL"
  fi
fi
export APP_HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
export APP_HTTP_PORT="${APP_HTTP_PORT:-8000}"
if [[ -n "${FREESURFER_LICENSE:-}" && "$FREESURFER_LICENSE" == "$NEUROCADE_HOST_DATA_DIR"/* ]]; then
  export FREESURFER_LICENSE="/data/${FREESURFER_LICENSE#"$NEUROCADE_HOST_DATA_DIR"/}"
else
  export FREESURFER_LICENSE="${FREESURFER_LICENSE:-}"
fi
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"

mkdir -p "$NEUROCADE_HOST_DATA_DIR/output" "$NEUROCADE_SIF_DIR" "$ROOT_DIR/llm-data/tool-catalog"

compose() {
  (cd "$ROOT_DIR" && docker compose "$@")
}
