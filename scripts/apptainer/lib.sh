#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer lib workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
if [[ -x "$ROOT_DIR/.node/bin/node" && -x "$ROOT_DIR/.node/bin/npm" ]]; then
  export PATH="$ROOT_DIR/.node/bin:$PATH"
fi
if [[ -x "$HOME/.local/bin/uv" ]]; then
  export PATH="$HOME/.local/bin:$PATH"
elif [[ -x "$HOME/.cargo/bin/uv" ]]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi

load_env_file() {
  local path="${1:-$ENV_FILE}"
  [[ -f "$path" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ "$key" == "UID" || "$key" == "EUID" || "$key" == "PPID" ]] && continue
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value#\"}"
      value="${value%\"}"
      value="${value//\\\"/\"}"
      value="${value//\\\$/\$}"
      value="${value//\\\`/\`}"
      value="${value//\\\\/\\}"
    fi
    export "$key=$value"
  done <"$path"
}

load_env_file

APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"
RUNTIME_DIR="${NEUROCADE_RUNTIME_DIR:-$ROOT_DIR/.runtime}"
APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-$ROOT_DIR/.apptainer/images}"
APPTAINER_CACHE_DIR="${APPTAINER_CACHE_DIR:-$ROOT_DIR/.apptainer/cache}"
HOST_DATA_DIR="${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
TOOL_CATALOG_DIR="${TOOL_CATALOG_DIR:-$ROOT_DIR/llm-data/tool-catalog}"
NEUROCADE_CONTAINER_ROOT="${NEUROCADE_CONTAINER_ROOT:-$ROOT_DIR/.apptainer/containers}"
NEUROCADE_CONTAINER_INVENTORY="${NEUROCADE_CONTAINER_INVENTORY:-$TOOL_CATALOG_DIR/installed_containers.json}"
NEUROCADE_INSTALLED_TOOLS_JSONL="${NEUROCADE_INSTALLED_TOOLS_JSONL:-$TOOL_CATALOG_DIR/installed_tools.jsonl}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
APP_HTTP_PORT="${APP_HTTP_PORT:-8005}"
TRAEFIK_DASHBOARD_BIND="${TRAEFIK_DASHBOARD_BIND:-127.0.0.1}"
TRAEFIK_DASHBOARD_PORT="${TRAEFIK_DASHBOARD_PORT:-8080}"

POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-55432}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-56379}"
API_SERVICE_HOST="${API_SERVICE_HOST:-127.0.0.1}"
API_SERVICE_PORT="${API_SERVICE_PORT:-58080}"
HOST_RUNTIME_RUNNER_HOST="${HOST_RUNTIME_RUNNER_HOST:-127.0.0.1}"
HOST_RUNTIME_RUNNER_PORT="${HOST_RUNTIME_RUNNER_PORT:-58081}"
HOST_RUNTIME_RUNNER_URL="${HOST_RUNTIME_RUNNER_URL:-http://$HOST_RUNTIME_RUNNER_HOST:$HOST_RUNTIME_RUNNER_PORT}"
CLIENT_HOST="${CLIENT_HOST:-127.0.0.1}"
CLIENT_PORT="${CLIENT_PORT:-5173}"
CLIENT_SERVE_MODE="${CLIENT_SERVE_MODE:-static}"

POSTGRES_USER="${POSTGRES_USER:-fastsurfer}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-fastsurfer}"
POSTGRES_DB="${POSTGRES_DB:-fastsurfer_app}"
REDIS_PASSWORD="${REDIS_PASSWORD:-fastsurfer-dev-redis}"

POSTGRES_SIF="${POSTGRES_SIF:-$APPTAINER_IMAGE_DIR/postgres-16-alpine.sif}"
REDIS_SIF="${REDIS_SIF:-$APPTAINER_IMAGE_DIR/redis-7-alpine.sif}"
TRAEFIK_SIF="${TRAEFIK_SIF:-$APPTAINER_IMAGE_DIR/traefik-v2.11.14.sif}"
POSTGRES_OCI="${POSTGRES_OCI:-docker://postgres:16-alpine}"
REDIS_OCI="${REDIS_OCI:-docker://redis:7.2.4-alpine}"
TRAEFIK_OCI="${TRAEFIK_OCI:-docker://traefik:v2.11.14}"
NEUROCADE_RELEASE_CONTAINER_BASE_URL="${NEUROCADE_RELEASE_CONTAINER_BASE_URL:-https://github.com/Deep-MI/NeuroCade/releases}"

release_asset_url() {
  local filename="$1"
  local tag="${NEUROCADE_CONTAINER_RELEASE_TAG:-latest}"
  if [[ -z "$tag" || "$tag" == "latest" ]]; then
    printf '%s/latest/download/%s\n' "${NEUROCADE_RELEASE_CONTAINER_BASE_URL%/}" "$filename"
  else
    printf '%s/download/%s/%s\n' "${NEUROCADE_RELEASE_CONTAINER_BASE_URL%/}" "$tag" "$filename"
  fi
}

POSTGRES_SIF_URL="${POSTGRES_SIF_URL:-$(release_asset_url "$(basename "$POSTGRES_SIF")")}"
REDIS_SIF_URL="${REDIS_SIF_URL:-$(release_asset_url "$(basename "$REDIS_SIF")")}"
TRAEFIK_SIF_URL="${TRAEFIK_SIF_URL:-$(release_asset_url "$(basename "$TRAEFIK_SIF")")}"

mkdir -p "$RUNTIME_DIR"/{logs,pids,postgres,postgres-run,redis,traefik,home,npm-cache} "$APPTAINER_IMAGE_DIR" "$APPTAINER_CACHE_DIR" "$HOST_DATA_DIR/output" "$TOOL_CATALOG_DIR" "$NEUROCADE_CONTAINER_ROOT"

RUNTIME_LOG_SERVICES=(
  postgres
  redis
  host-runtime-runner
  api-service
  api-worker
  client
  update-checker
  traefik
)

HOST_RUNTIME_RUNNER_TOKEN="${HOST_RUNTIME_RUNNER_TOKEN:-}"
if [[ -z "$HOST_RUNTIME_RUNNER_TOKEN" ]]; then
  HOST_RUNTIME_RUNNER_TOKEN_FILE="${HOST_RUNTIME_RUNNER_TOKEN_FILE:-$RUNTIME_DIR/host-runtime-runner.token}"
  if [[ ! -f "$HOST_RUNTIME_RUNNER_TOKEN_FILE" ]]; then
    umask 077
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 32 > "$HOST_RUNTIME_RUNNER_TOKEN_FILE"
    else
      set +o pipefail
      LC_ALL=C tr -dc 'A-Fa-f0-9' </dev/urandom | head -c 64 > "$HOST_RUNTIME_RUNNER_TOKEN_FILE"
      set -o pipefail
      printf '\n' >> "$HOST_RUNTIME_RUNNER_TOKEN_FILE"
    fi
  fi
  HOST_RUNTIME_RUNNER_TOKEN="$(<"$HOST_RUNTIME_RUNNER_TOKEN_FILE")"
fi

export APPTAINER_CACHEDIR="$APPTAINER_CACHE_DIR"
export HOST_DATA_DIR POSTGRES_HOST POSTGRES_PORT REDIS_HOST REDIS_PORT
export APPTAINER_BIN
export TOOL_CATALOG_DIR NEUROCADE_CONTAINER_ROOT NEUROCADE_CONTAINER_INVENTORY NEUROCADE_INSTALLED_TOOLS_JSONL
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB}"
export REDIS_URL="${REDIS_URL:-redis://:$REDIS_PASSWORD@$REDIS_HOST:$REDIS_PORT/0}"
export API_SERVICE_URL="${API_SERVICE_URL:-http://$API_SERVICE_HOST:$API_SERVICE_PORT}"
export HOST_RUNTIME_RUNNER_HOST HOST_RUNTIME_RUNNER_PORT HOST_RUNTIME_RUNNER_URL HOST_RUNTIME_RUNNER_TOKEN
export CLIENT_SERVE_MODE

project_python_minor_version() {
  command -v uv >/dev/null 2>&1 || return 1
  local version major minor rest
  version="$(uv --project "$ROOT_DIR" python find --show-version 2>/dev/null || true)"
  [[ -n "$version" ]] || return 1
  major="${version%%.*}"
  rest="${version#*.}"
  minor="${rest%%.*}"
  printf '%s.%s\n' "$major" "$minor"
}

python_bin() {
  local python_path="$ROOT_DIR/.venv/bin/python"
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    local venv_version project_version
    venv_version="$("$python_path" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    project_version="$(project_python_minor_version || true)"
    if [[ -n "$project_version" && "$venv_version" != "$project_version" ]]; then
      echo "NeuroCade requires a .venv compatible with pyproject.toml, but .venv uses Python $venv_version." >&2
      echo "Recreate it with: uv venv --project \"$ROOT_DIR\" \"$ROOT_DIR/.venv\"" >&2
      exit 1
    fi
    printf '%s\n' "$python_path"
    return
  fi
  cat >&2 <<EOF
NeuroCade requires a project Python virtualenv at $ROOT_DIR/.venv.

Create it with uv, then rerun:
  cd "$ROOT_DIR"
  uv venv --project "$ROOT_DIR" "$ROOT_DIR/.venv"
  uv pip install --python "$ROOT_DIR/.venv/bin/python" -r "$ROOT_DIR/pyproject.toml"
EOF
  exit 1
}

service_pid_file() {
  printf '%s/pids/%s.pid\n' "$RUNTIME_DIR" "$1"
}

service_log_file() {
  printf '%s/logs/%s.log\n' "$RUNTIME_DIR" "$1"
}

file_size_bytes() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    printf '0\n'
    return 0
  fi
  wc -c <"$path" | tr -d '[:space:]'
}

is_service_running() {
  local pid_file
  pid_file="$(service_pid_file "$1")"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

uses_lima_apptainer() {
  [[ "$APPTAINER_BIN" == "$ROOT_DIR/.apptainer/bin/apptainer" ]] && command -v limactl >/dev/null 2>&1
}

stop_lima_orphan() {
  local name="$1"
  uses_lima_apptainer || return 0

  local port=""
  case "$name" in
    postgres)
      port="$POSTGRES_PORT"
      ;;
    redis)
      port="$REDIS_PORT"
      ;;
    traefik)
      port="$APP_HTTP_PORT"
      ;;
    *)
      return 0
      ;;
  esac

  limactl shell apptainer sh -lc '
    port=":$1"
    pids="$(ss -ltnp 2>/dev/null | awk -v port="$port" '"'"'$4 ~ port { print }'"'"' | sed -n '"'"'s/.*pid=\([0-9][0-9]*\).*/\1/p'"'"' | sort -u)"
    for pid in $pids; do
      kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in $pids; do
      kill -KILL "$pid" 2>/dev/null || true
    done
  ' sh "$port" >/dev/null 2>&1 || true
}

start_service() {
  local name="$1"
  shift
  local log_file pid_file
  log_file="$(service_log_file "$name")"
  pid_file="$(service_pid_file "$name")"
  if is_service_running "$name"; then
    echo "$name already running (pid $(cat "$pid_file"))"
    return 0
  fi
  echo "Starting $name"
  : >"$log_file"
  (
    cd "$ROOT_DIR"
    if command -v setsid >/dev/null 2>&1; then
      exec setsid "$@"
    fi
    if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
      exec "$ROOT_DIR/.venv/bin/python" -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
    fi
    exec nohup "$@"
  ) </dev/null >>"$log_file" 2>&1 &
  echo "$!" >"$pid_file"
}

stop_service() {
  local name="$1"
  local pid_file pid
  pid_file="$(service_pid_file "$name")"
  if [[ ! -f "$pid_file" ]]; then
    stop_lima_orphan "$name"
    return 0
  fi
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping $name"
    kill "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in {1..240}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  fi
  stop_lima_orphan "$name"
  rm -f "$pid_file"
}

require_apptainer() {
  if ! command -v "$APPTAINER_BIN" >/dev/null 2>&1; then
    cat >&2 <<EOF
Apptainer is required but '$APPTAINER_BIN' was not found.

Run the installer so NeuroCade can prepare Apptainer for this host:
  ./scripts/install.sh --mode local

Linux: the installer uses Apptainer's unprivileged install path.
macOS: the installer prepares a Lima-backed Apptainer runtime when Homebrew is available.
EOF
    exit 1
  fi
}

wait_for_url() {
  local url="$1"
  local timeout="${2:-120}"
  local deadline=$((SECONDS + timeout))
  until curl -fsS "$url" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for $url" >&2
      return 1
    fi
    sleep 1
  done
}
