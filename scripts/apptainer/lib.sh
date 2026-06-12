#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer lib workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
source "$ROOT_DIR/scripts/runtime_cache_env.sh"
if [[ -x "$ROOT_DIR/.node/bin/node" && -x "$ROOT_DIR/.node/bin/npm" ]]; then
  export PATH="$ROOT_DIR/.node/bin:$PATH"
fi
if [[ -x "$ROOT_DIR/.runtime/uv/bin/uv" ]]; then
  export PATH="$ROOT_DIR/.runtime/uv/bin:$PATH"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
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
RELEASE_CONTAINER_BASE_URL="https://github.com/Deep-MI/NeuroCade/releases"

ensure_uv_state_dir() {
  local uv_state_dir="${1:-$RUNTIME_DIR/uv}"

  mkdir -p "$uv_state_dir/config" "$uv_state_dir/cache"

  if [[ -z "${XDG_CONFIG_HOME:-}" || "${XDG_CONFIG_HOME:-}" != /* ]] || ! mkdir -p "$XDG_CONFIG_HOME" 2>/dev/null || [[ ! -w "$XDG_CONFIG_HOME" ]]; then
    export XDG_CONFIG_HOME="$uv_state_dir/config"
  fi
  if [[ -z "${UV_CACHE_DIR:-}" || "${UV_CACHE_DIR:-}" != /* ]] || ! mkdir -p "$UV_CACHE_DIR" 2>/dev/null || [[ ! -w "$UV_CACHE_DIR" ]]; then
    export UV_CACHE_DIR="$uv_state_dir/cache"
  fi
}

release_asset_url() {
  local filename="$1"
  local tag="${NEUROCADE_CONTAINER_RELEASE_TAG:-latest}"
  if [[ -z "$tag" || "$tag" == "latest" ]]; then
    printf '%s/latest/download/%s\n' "${RELEASE_CONTAINER_BASE_URL%/}" "$filename"
  else
    printf '%s/download/%s/%s\n' "${RELEASE_CONTAINER_BASE_URL%/}" "$tag" "$filename"
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

lima_instance_running() {
  uses_lima_apptainer || return 0
  limactl list --format '{{.Name}} {{.Status}}' 2>/dev/null | awk '$1 == "apptainer" && $2 == "Running" { found = 1 } END { exit(found ? 0 : 1) }'
}

lima_checkout_mount_live_writable() {
  uses_lima_apptainer || return 0
  lima_instance_running || return 1
  limactl shell apptainer sh -c '
root="$1"
mkdir -p "$root/.runtime" || exit 1
probe="$root/.runtime/.neurocade-lima-write-probe.$$"
: >"$probe" && rm -f "$probe"
' sh "$ROOT_DIR" >/dev/null 2>&1
}

ensure_lima_checkout_mount_live_writable() {
  uses_lima_apptainer || return 0
  if lima_checkout_mount_live_writable; then
    return 0
  fi
  if lima_instance_running; then
    echo "Lima checkout mount is not writable; restarting the Apptainer VM to refresh mounts."
    limactl stop apptainer
  else
    echo "Lima Apptainer VM is stopped; starting it before checking checkout mounts."
  fi
  limactl start apptainer
  if ! lima_checkout_mount_live_writable; then
    cat >&2 <<EOF
The NeuroCade checkout is not writable inside the Lima Apptainer VM:
  $ROOT_DIR

Check ~/.lima/apptainer/lima.yaml and make sure this checkout is mounted with:
  - location: "$ROOT_DIR"
    writable: true
EOF
    return 1
  fi
}

normalize_container_arch() {
  local arch
  arch="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$arch" in
    amd64|x86_64)
      printf 'amd64\n'
      ;;
    arm64|aarch64)
      printf 'arm64\n'
      ;;
    *)
      printf '%s\n' "$arch"
      ;;
  esac
}

apptainer_guest_arch() {
  if uses_lima_apptainer; then
    limactl shell apptainer uname -m < /dev/null
    return
  fi
  uname -m
}

image_build_arch() {
  local image="$1"
  { "$APPTAINER_BIN" inspect --json "$image" < /dev/null 2>/dev/null || true; } \
    | sed -n 's/.*"org.label-schema.build-arch"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | sed -n '1p'
}

image_runtime_arch() {
  local image="$1"
  "$APPTAINER_BIN" exec "$image" uname -m < /dev/null 2>/dev/null | sed -n '1p'
}

image_compatible_with_guest() {
  local image="$1"
  local guest_arch_raw="$2"
  local guest_arch image_arch_raw image_arch runtime_arch_raw runtime_arch
  [[ -n "$guest_arch_raw" ]] || return 1
  guest_arch="$(normalize_container_arch "$guest_arch_raw")"
  image_arch_raw="$(image_build_arch "$image")"
  if [[ -n "$image_arch_raw" ]]; then
    image_arch="$(normalize_container_arch "$image_arch_raw")"
    [[ "$image_arch" == "$guest_arch" ]] || return 1
  fi
  runtime_arch_raw="$(image_runtime_arch "$image" || true)"
  [[ -n "$runtime_arch_raw" ]] || return 1
  runtime_arch="$(normalize_container_arch "$runtime_arch_raw")"
  [[ "$runtime_arch" == "$guest_arch" ]]
}

list_startup_container_images() {
  printf '%s\n' "$POSTGRES_SIF" "$REDIS_SIF" "$TRAEFIK_SIF"
}

check_system_container_arch_compatibility() {
  require_apptainer
  local guest_arch_raw guest_arch image image_arch_raw image_arch runtime_arch_raw runtime_arch failures
  local checked_images=""
  guest_arch_raw="$(apptainer_guest_arch 2>/dev/null || true)"
  if [[ -z "$guest_arch_raw" ]]; then
    echo "Could not determine Apptainer guest architecture." >&2
    return 1
  fi
  guest_arch="$(normalize_container_arch "$guest_arch_raw")"
  failures=0
  while IFS= read -r image; do
    [[ -n "$image" && -f "$image" ]] || continue
    if [[ "$checked_images" == *"|$image|"* ]]; then
      continue
    fi
    checked_images="${checked_images}|$image|"
    image_arch_raw="$(image_build_arch "$image")"
    if [[ -z "$image_arch_raw" ]]; then
      echo "Warning: could not determine image architecture for $image" >&2
    else
      image_arch="$(normalize_container_arch "$image_arch_raw")"
      if [[ "$image_arch" != "$guest_arch" ]]; then
        echo "Incompatible container architecture: $image is $image_arch_raw, but the Apptainer guest is $guest_arch_raw." >&2
        failures=1
        continue
      fi
    fi
    runtime_arch_raw="$(image_runtime_arch "$image" || true)"
    if [[ -z "$runtime_arch_raw" ]]; then
      echo "Incompatible container architecture: $image could not be executed by the current Apptainer guest ($guest_arch_raw)." >&2
      failures=1
      continue
    fi
    runtime_arch="$(normalize_container_arch "$runtime_arch_raw")"
    if [[ "$runtime_arch" != "$guest_arch" ]]; then
      echo "Incompatible container architecture: $image runs as $runtime_arch_raw, but the Apptainer guest is $guest_arch_raw." >&2
      failures=1
    else
      echo "Container architecture ok: $image (${image_arch_raw:-unknown label}, runs $runtime_arch_raw on $guest_arch_raw)"
    fi
  done < <(list_startup_container_images)

  if (( failures )); then
    cat >&2 <<EOF
One or more NeuroCade system containers cannot run in the current Apptainer guest.

On Apple Silicon Macs, use an amd64/x86_64 Lima VM for the current release
(NEUROCADE_LIMA_ARCH=x86_64), or publish arm64-compatible system container
images before using a native arm64/aarch64 Lima guest.
EOF
    return 1
  fi
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

stop_host_orphan() {
  local name="$1"
  local port="" pattern=""
  case "$name" in
    host-runtime-runner)
      port="$HOST_RUNTIME_RUNNER_PORT"
      pattern="api_service.host_runtime_runner"
      ;;
    api-service)
      port="$API_SERVICE_PORT"
      pattern="api_service.main"
      ;;
    client)
      port="$CLIENT_PORT"
      pattern="vite|serve_static_client.py"
      ;;
    *)
      return 0
      ;;
  esac

  command -v lsof >/dev/null 2>&1 || return 0
  local pids pid command_line remaining
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  for pid in $pids; do
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -n "$command_line" ]] || continue
    if [[ "$command_line" =~ $pattern ]] && { [[ "$command_line" == *"$ROOT_DIR"* ]] || [[ "$command_line" == *"NeuroCade"* ]]; }; then
      echo "Stopping stale $name listener on port $port (pid $pid)"
      kill "$pid" 2>/dev/null || true
    fi
  done
  for _ in {1..20}; do
    remaining=0
    for pid in $pids; do
      command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      [[ -n "$command_line" ]] || continue
      if [[ "$command_line" =~ $pattern ]] && { [[ "$command_line" == *"$ROOT_DIR"* ]] || [[ "$command_line" == *"NeuroCade"* ]]; }; then
        remaining=1
      fi
    done
    (( remaining )) || break
    sleep 0.1
  done
  for pid in $pids; do
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -n "$command_line" ]] || continue
    if [[ "$command_line" =~ $pattern ]] && { [[ "$command_line" == *"$ROOT_DIR"* ]] || [[ "$command_line" == *"NeuroCade"* ]]; }; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  sleep 0.2
  for pid in $pids; do
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -n "$command_line" ]] || continue
    if [[ "$command_line" =~ $pattern ]] && { [[ "$command_line" == *"$ROOT_DIR"* ]] || [[ "$command_line" == *"NeuroCade"* ]]; }; then
      echo "Warning: $name listener on port $port is still running after shutdown attempt (pid $pid)." >&2
    fi
  done
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
  stop_host_orphan "$name"
  stop_lima_orphan "$name"
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
    stop_host_orphan "$name"
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
  stop_host_orphan "$name"
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
