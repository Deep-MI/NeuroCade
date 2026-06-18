#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

load_env_file() {
  [[ -f "$ENV_FILE" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n "${!key+x}" ]] && continue
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value#\"}"
      value="${value%\"}"
      value="${value//\\\"/\"}"
      value="${value//\\\$/\$}"
      value="${value//\\\`/\`}"
      value="${value//\\\\/\\}"
    fi
    export "$key=$value"
  done <"$ENV_FILE"
}

load_env_file

export NEUROCADE_HOST_DATA_DIR="${NEUROCADE_HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
export HOST_DATA_DIR="/data"
export APP_HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
export APP_HTTP_PORT="${APP_HTTP_PORT:-8005}"
export RUNTIME_RUNNER_TOKEN="${RUNTIME_RUNNER_TOKEN:-dev-runtime-runner-token}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-fastsurfer}"
export REDIS_PASSWORD="${REDIS_PASSWORD:-fastsurfer-dev-redis}"
if [[ -n "${FREESURFER_LICENSE:-}" && "$FREESURFER_LICENSE" == "$NEUROCADE_HOST_DATA_DIR"/* ]]; then
  export FREESURFER_LICENSE="/data/${FREESURFER_LICENSE#"$NEUROCADE_HOST_DATA_DIR"/}"
else
  export FREESURFER_LICENSE="${FREESURFER_LICENSE:-}"
fi
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"

mkdir -p "$NEUROCADE_HOST_DATA_DIR/output" "$ROOT_DIR/.runtime/docker/postgres" "$ROOT_DIR/.runtime/docker/redis" "$ROOT_DIR/llm-data/tool-catalog"

compose() {
  (cd "$ROOT_DIR" && docker compose "$@")
}
