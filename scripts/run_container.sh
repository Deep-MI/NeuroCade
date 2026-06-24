#!/usr/bin/env bash
# Run the NeuroCade monolith container. One service, so no docker compose needed.
#
# Apptainer runs *inside* this container, which requires elevated privileges
# (--privileged + /dev/fuse). The native install (scripts/desktop/run_backend.sh)
# needs none of that and is the recommended default.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

IMAGE="${NEUROCADE_IMAGE:-neurocade:local}"
HOST_DATA_DIR="${NEUROCADE_HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
HTTP_PORT="${APP_HTTP_PORT:-8000}"
CONTAINER_DATABASE_URL="${NEUROCADE_CONTAINER_DATABASE_URL:-}"
if [[ -z "$CONTAINER_DATABASE_URL" ]]; then
  if [[ -z "${DATABASE_URL:-}" || "$DATABASE_URL" == sqlite:* ]]; then
    CONTAINER_DATABASE_URL="sqlite+pysqlite:////data/neurocade.db"
  else
    CONTAINER_DATABASE_URL="$DATABASE_URL"
  fi
fi
mkdir -p "$HOST_DATA_DIR"

env_file_args=()
[[ -f "$ENV_FILE" ]] && env_file_args=(--env-file "$ENV_FILE")

exec docker run --rm --name neurocade \
  --privileged --device /dev/fuse \
  -p "${HTTP_BIND}:${HTTP_PORT}:8000" \
  -v "${HOST_DATA_DIR}:/data" \
  "${env_file_args[@]}" \
  -e HOST_DATA_DIR=/data \
  -e NEUROCADE_SIF_DIR=/data/sif \
  -e "DATABASE_URL=$CONTAINER_DATABASE_URL" \
  -e "NEUROCADE_RUNTIME_BACKEND=${NEUROCADE_RUNTIME_BACKEND:-apptainer}" \
  "$IMAGE" "$@"
