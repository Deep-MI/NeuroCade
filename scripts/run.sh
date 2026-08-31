#!/usr/bin/env bash
# Launch NeuroCade with a host-native bridge and a matched Docker or Apptainer app.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/env.sh"
source "$ROOT_DIR/scripts/lib/managed_python.sh"
source "$ROOT_DIR/scripts/lib/docker_cli.sh"
load_env_file
configure_docker_cli_path

RUNTIME="${NEUROCADE_RUNTIME:-}"
HOST_DATA_DIR="${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
[[ "$HOST_DATA_DIR" == /* ]] || HOST_DATA_DIR="$ROOT_DIR/$HOST_DATA_DIR"
RUNTIME_DIR="$ROOT_DIR/.runtime"
APPTAINER_DATABASE_DIR="$RUNTIME_DIR/database"
IMAGE_DIR="$RUNTIME_DIR/images"
BRIDGE_VENV="$RUNTIME_DIR/bridge-venv"
BRIDGE_BIN="$BRIDGE_VENV/bin/neurocade-runtime-bridge"
BRIDGE_TOKEN_FILE="$RUNTIME_DIR/bridge-token"
BRIDGE_PID_FILE="$RUNTIME_DIR/bridge.pid"
BRIDGE_LOG="$RUNTIME_DIR/bridge.log"
LAUNCH_ID_FILE="$RUNTIME_DIR/launch-id"
LAUNCHER_LOCK_DIR="$RUNTIME_DIR/launcher.lock"
APP_PID_FILE="$RUNTIME_DIR/app.pid"
APP_LOG="$RUNTIME_DIR/app.log"
APP_URL_FILE="$RUNTIME_DIR/app-url"
BRIDGE_PORT="${NEUROCADE_BRIDGE_PORT:-8765}"
HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
HTTP_PORT="${APP_HTTP_PORT:-8000}"
STARTUP_TIMEOUT_SECONDS="${NEUROCADE_STARTUP_TIMEOUT_SECONDS:-120}"
IMAGE="${NEUROCADE_IMAGE:-ghcr.io/deep-mi/neurocade:latest}"
CONTAINER_NAME="${NEUROCADE_CONTAINER_NAME:-neurocade}"
DATABASE_VOLUME="${NEUROCADE_DATABASE_VOLUME:-neurocade-database}"
DOCKER_PLATFORM="${NEUROCADE_DOCKER_PLATFORM:-}"
APP_SIF_MODE="${NEUROCADE_APP_SIF_MODE:-}"
APP_SIF="$IMAGE_DIR/neurocade-app-amd64.sif"
BRIDGE_PACKAGE="${NEUROCADE_BRIDGE_PACKAGE:-$ROOT_DIR/packages/neurocade-runtime-tools}"
TOOL_MANIFEST="$ROOT_DIR/config/tool_images.json"
SAMPLE_CASE_DIR="$ROOT_DIR/sample_case"
SAMPLE_CASE_NAME="${NEUROCADE_SAMPLE_CASE_NAME:-FastSurfer_Rhineland_0000}"
SAMPLE_CASE_URL="${NEUROCADE_SAMPLE_CASE_URL:-https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.7/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz}"
SAMPLE_CASE_SHA256="${NEUROCADE_SAMPLE_CASE_SHA256:-71814b4687180e10543523bb07292725f4b165acce7d0f9d34148028daa061b7}"

usage() {
  cat <<'EOF'
Usage: ./scripts/run.sh [start|stop|status|logs|pull|build|prepare-tools|doctor] [options]

NEUROCADE_RUNTIME=docker|apptainer is required and selects both the application
artifact and the host tool runtime. Start options: -d, --detach, --build, --port PORT.
EOF
}

fail() { echo "ERROR: $*" >&2; exit 1; }
truthy() { case "${1:-}" in 1|true|TRUE|yes|YES|on|ON) return 0 ;; *) return 1 ;; esac; }

print_browser_url() {
  local browser_url="$1"
  if [[ -n "${NO_COLOR+x}" || ! -t 1 ]]; then
    printf 'Open NeuroCade in a browser at %s\n' "$browser_url"
    return
  fi
  printf '\033[1;32mOpen NeuroCade in a browser at\033[0m \033[1;36m%s\033[0m\n' "$browser_url"
}

validate_configuration() {
  case "$RUNTIME" in docker|apptainer) ;; *) fail "NEUROCADE_RUNTIME=docker|apptainer is required. Rerun scripts/install.sh --runtime docker|apptainer." ;; esac
  [[ "$BRIDGE_PORT" =~ ^[0-9]+$ ]] && (( BRIDGE_PORT > 0 && BRIDGE_PORT < 65536 )) || fail "Invalid NEUROCADE_BRIDGE_PORT"
  [[ "$HTTP_PORT" =~ ^[0-9]+$ ]] && (( HTTP_PORT > 0 && HTTP_PORT < 65536 )) || fail "Invalid APP_HTTP_PORT"
  [[ "$DATABASE_VOLUME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] || fail "Invalid NEUROCADE_DATABASE_VOLUME"
  if [[ "$RUNTIME" == "docker" ]]; then
    command -v docker >/dev/null 2>&1 || fail "Docker is required for the docker profile"
  else
    case "$APP_SIF_MODE" in release|source) ;; *) fail "The Apptainer artifact mode is missing; rerun scripts/install.sh" ;; esac
    [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || fail "Apptainer profile currently requires Linux amd64"
    [[ "$(id -u)" -ne 0 ]] || fail "The rootless Apptainer profile must be run as a non-root user"
    [[ -r /proc/sys/user/max_user_namespaces ]] && (( $(cat /proc/sys/user/max_user_namespaces) > 0 )) || fail "User namespaces are required"
    command -v apptainer >/dev/null 2>&1 || fail "Apptainer is required for the apptainer profile"
    apptainer exec --help 2>&1 | grep -q -- '--no-home' || fail "Apptainer does not support required rootless flags"
  fi
}

ensure_directories() {
  mkdir -p "$RUNTIME_DIR" "$IMAGE_DIR" "$HOST_DATA_DIR/output" "$HOST_DATA_DIR/.tmp" "$APPTAINER_DATABASE_DIR" "$SAMPLE_CASE_DIR"
}

ensure_bridge_environment() {
  local python_bin managed_identity bridge_identity=""
  require_managed_uv || fail "Managed Python tooling is unavailable"
  python_bin="$(managed_python_path)" || fail "Managed Python $NEUROCADE_PYTHON_VERSION is missing; rerun scripts/install.sh"
  managed_identity="$(python_runtime_identity "$python_bin")"
  if [[ -x "$BRIDGE_VENV/bin/python" ]]; then
    bridge_identity="$(python_runtime_identity "$BRIDGE_VENV/bin/python" 2>/dev/null || true)"
  fi
  if [[ "$bridge_identity" != "$managed_identity" ]]; then
    managed_uv venv --clear --python "$python_bin" "$BRIDGE_VENV"
  fi
  [[ -e "$BRIDGE_PACKAGE" ]] || fail "The matched runtime bridge package is missing; rerun scripts/install.sh"
  managed_uv pip install --reinstall --python "$BRIDGE_VENV/bin/python" "$BRIDGE_PACKAGE"
  [[ -x "$BRIDGE_BIN" ]] || fail "Managed runtime bridge installation failed"
}

python_runtime_identity() {
  "$1" -c 'import platform; print(f"{platform.python_version()}:{platform.machine()}")'
}

report_managed_toolchain() {
  local process_arch host_arch python_bin python_details
  process_arch="$(neurocade_process_arch)"
  host_arch="$(neurocade_host_arch)"
  echo "OK: host is $(uname -s) $host_arch"
  if neurocade_is_rosetta; then
    echo "INFO: installer shell uses Rosetta ($process_arch); managed tools target native $host_arch"
  fi
  echo "OK: $("$MANAGED_UV_BIN" --version) at $MANAGED_UV_BIN"
  python_bin="$(managed_python_path)" || fail "Managed Python $NEUROCADE_PYTHON_VERSION is missing"
  python_details="$("$python_bin" -c 'import platform; print(f"Python {platform.python_version()} ({platform.machine()})")')"
  echo "OK: $python_details at $python_bin"
  [[ -z "$DOCKER_PLATFORM" ]] || echo "OK: Docker application platform is $DOCKER_PLATFORM"
  if [[ "$host_arch" == "arm64" && "$python_details" != *"(arm64)"* ]]; then
    echo "WARN: managed Python is translated; rerun scripts/install.sh to install native arm64 tools" >&2
  fi
}

ensure_token() {
  if [[ ! -s "$BRIDGE_TOKEN_FILE" ]]; then
    umask 077
    "$BRIDGE_VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))' >"$BRIDGE_TOKEN_FILE"
  fi
  chmod 600 "$BRIDGE_TOKEN_FILE"
}

load_launch_id() {
  LAUNCH_ID=""
  [[ -s "$LAUNCH_ID_FILE" ]] && LAUNCH_ID="$(sed -n '1p' "$LAUNCH_ID_FILE")"
  return 0
}

new_launch_id() {
  umask 077
  LAUNCH_ID="$("$BRIDGE_VENV/bin/python" -c 'import uuid; print(uuid.uuid4())')"
  printf '%s\n' "$LAUNCH_ID" >"$LAUNCH_ID_FILE"
  chmod 600 "$LAUNCH_ID_FILE"
}

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .
    return
  fi
  return 1
}

select_http_port() {
  local requested="$HTTP_PORT"
  while port_in_use "$HTTP_PORT"; do
    (( HTTP_PORT < 65535 )) || fail "No available application port"
    HTTP_PORT=$((HTTP_PORT + 1))
  done
  [[ "$HTTP_PORT" == "$requested" ]] || echo "Port $requested is occupied; using $HTTP_PORT."
}

pid_matches() {
  local pid_file="$1" identity="$2" pid command_line owner_uid
  [[ -f "$pid_file" ]] || return 1
  pid="$(sed -n '1p' "$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  owner_uid="$(ps -p "$pid" -o uid= 2>/dev/null | tr -d ' ')"
  [[ "$owner_uid" == "$(id -u)" ]] || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"$identity"* ]]
}

bridge_health() {
  [[ -n "${LAUNCH_ID:-}" ]] || return 1
  "$BRIDGE_VENV/bin/python" -c 'import sys,requests; from neurocade_runtime_tools.protocol import PROTOCOL_VERSION; h={"Authorization":"Bearer "+open(sys.argv[2]).read().strip(),"X-NeuroCade-Launch-ID":sys.argv[4]}; r=requests.get(sys.argv[1]+"/v1/health",headers=h,timeout=5); r.raise_for_status(); p=r.json(); expected={"protocol_version":PROTOCOL_VERSION,"backend":sys.argv[3],"launch_id":sys.argv[4],"docker_platform":sys.argv[5] or None,"data_root":sys.argv[6],"image_dir":sys.argv[7]}; raise SystemExit(0 if all(p.get(k)==v for k,v in expected.items()) else 1)' \
    "http://127.0.0.1:$BRIDGE_PORT" "$BRIDGE_TOKEN_FILE" "$RUNTIME" "$LAUNCH_ID" \
    "$DOCKER_PLATFORM" "$HOST_DATA_DIR" "$IMAGE_DIR" >/dev/null 2>&1
}

bridge_active_runs() {
  local launch_header="${LAUNCH_ID:-}"
  "$BRIDGE_VENV/bin/python" -c 'import sys,requests; h={"Authorization":"Bearer "+open(sys.argv[2]).read().strip()}; sys.argv[3] and h.update({"X-NeuroCade-Launch-ID":sys.argv[3]}); r=requests.get(sys.argv[1]+"/v1/health",headers=h,timeout=5); r.raise_for_status(); print(int(r.json().get("active_runs",0)))' \
    "http://127.0.0.1:$BRIDGE_PORT" "$BRIDGE_TOKEN_FILE" "$launch_header" 2>/dev/null
}

begin_launch_session() {
  local active_runs=""
  load_launch_id
  if pid_matches "$BRIDGE_PID_FILE" "neurocade-runtime-bridge"; then
    active_runs="$(bridge_active_runs || true)"
    [[ "$active_runs" =~ ^[0-9]+$ ]] || fail \
      "The existing runtime bridge cannot be identified safely; run ./scripts/run.sh stop before restarting"
    (( active_runs == 0 )) || fail \
      "The previous application stopped while $active_runs workflow(s) remain active; wait for them to finish before restarting"
    echo "Restarting the runtime bridge for a new application launch session."
    stop_bridge
  fi
  new_launch_id
}

start_bridge() {
  if pid_matches "$BRIDGE_PID_FILE" "neurocade-runtime-bridge"; then
    local existing_deadline=$((SECONDS + 15))
    while (( SECONDS < existing_deadline )); do
      bridge_health && return
      sleep 1
    done
    fail "The existing runtime bridge process is not healthy; run ./scripts/run.sh stop before restarting"
  fi
  if [[ -f "$BRIDGE_PID_FILE" ]]; then
    echo "Removing stale bridge PID file." >&2
    rm -f "$BRIDGE_PID_FILE"
  fi
  port_in_use "$BRIDGE_PORT" && fail "Bridge port $BRIDGE_PORT is already occupied by an unmanaged process"
  local bind_host=127.0.0.1
  [[ "$RUNTIME" == "docker" ]] && bind_host=0.0.0.0
  "$BRIDGE_BIN" serve --runtime "$RUNTIME" --data-root "$HOST_DATA_DIR" --image-dir "$IMAGE_DIR" \
    --host "$bind_host" --port "$BRIDGE_PORT" --token-file "$BRIDGE_TOKEN_FILE" \
    --launch-id "$LAUNCH_ID" \
    --daemonize --pid-file "$BRIDGE_PID_FILE" --log-file "$BRIDGE_LOG"
  local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    bridge_health && return
    pid_matches "$BRIDGE_PID_FILE" "neurocade-runtime-bridge" || { tail -n 100 "$BRIDGE_LOG" >&2 || true; fail "Runtime bridge exited during startup"; }
    sleep 1
  done
  fail "Runtime bridge did not become healthy"
}

stop_pid_file() {
  local pid_file="$1" identity="$2" pid deadline
  pid_matches "$pid_file" "$identity" || { rm -f "$pid_file"; return; }
  pid="$(sed -n '1p' "$pid_file")"
  kill -TERM "$pid" 2>/dev/null || true
  deadline=$((SECONDS + 15))
  while kill -0 "$pid" 2>/dev/null && (( SECONDS < deadline )); do sleep 1; done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$pid_file"
}

prepare_tools() {
  "$BRIDGE_BIN" prepare-images --runtime "$RUNTIME" --data-root "$HOST_DATA_DIR" --image-dir "$IMAGE_DIR" --manifest "$TOOL_MANIFEST"
}

ensure_sample_case() {
  truthy "${NEUROCADE_SKIP_SAMPLE_CASE:-false}" && return
  [[ -d "$SAMPLE_CASE_DIR/$SAMPLE_CASE_NAME" ]] && return
  local archive="$RUNTIME_DIR/sample-case.tar.gz"
  "$BRIDGE_BIN" download-verified --url "$SAMPLE_CASE_URL" --sha256 "$SAMPLE_CASE_SHA256" --target "$archive"
  tar -xzf "$archive" -C "$SAMPLE_CASE_DIR"
}

ensure_application() {
  runtime_application_exists || runtime_pull_application
}

application_health() {
  local base_url="http://127.0.0.1:$HTTP_PORT"
  [[ -s "$APP_URL_FILE" ]] && base_url="$(sed -n '1p' "$APP_URL_FILE")"
  "$BRIDGE_VENV/bin/python" -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1],timeout=2).read()' "$base_url/api/app/healthz" >/dev/null 2>&1
}

write_application_url() {
  local display_host="$HTTP_BIND"
  [[ "$display_host" == "0.0.0.0" || "$display_host" == "::" ]] && display_host=localhost
  printf 'http://%s:%s\n' "$display_host" "$HTTP_PORT" >"$APP_URL_FILE"
}

wait_for_application() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do application_health && return; sleep 1; done
  tail -n 100 "$APP_LOG" >&2 2>/dev/null || true
  runtime_tail_application_logs
  fail "NeuroCade did not become healthy"
}

stop_application() {
  runtime_stop_application
}
stop_bridge() { stop_pid_file "$BRIDGE_PID_FILE" "neurocade-runtime-bridge"; }

release_launcher_lock() {
  local owner=""
  [[ -f "$LAUNCHER_LOCK_DIR/pid" ]] && owner="$(sed -n '1p' "$LAUNCHER_LOCK_DIR/pid")"
  if [[ "$owner" == "$$" ]]; then
    rm -f "$LAUNCHER_LOCK_DIR/pid"
    rmdir "$LAUNCHER_LOCK_DIR" 2>/dev/null || true
  fi
}

acquire_launcher_lock() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS)) owner=""
  while ! mkdir "$LAUNCHER_LOCK_DIR" 2>/dev/null; do
    [[ -f "$LAUNCHER_LOCK_DIR/pid" ]] && owner="$(sed -n '1p' "$LAUNCHER_LOCK_DIR/pid")"
    if [[ ! "$owner" =~ ^[0-9]+$ ]] || ! kill -0 "$owner" 2>/dev/null; then
      rm -f "$LAUNCHER_LOCK_DIR/pid"
      rmdir "$LAUNCHER_LOCK_DIR" 2>/dev/null || true
      continue
    fi
    (( SECONDS < deadline )) || fail "Another NeuroCade launcher operation is still running (PID $owner)"
    sleep 1
  done
  printf '%s\n' "$$" >"$LAUNCHER_LOCK_DIR/pid"
  trap release_launcher_lock EXIT
}

COMMAND="${1:-start}"
case "$COMMAND" in start|stop|status|logs|pull|build|prepare-tools|doctor) shift || true ;; -h|--help) usage; exit 0 ;; *) COMMAND=start ;; esac
DETACH=0
BUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--detach) DETACH=1; shift ;;
    --build) BUILD=1; shift ;;
    --port) [[ $# -ge 2 ]] || fail "--port requires a value"; HTTP_PORT="$2"; shift 2 ;;
    --port=*) HTTP_PORT="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

validate_configuration
ensure_directories
source "$ROOT_DIR/scripts/lib/runtime_${RUNTIME}.sh"
case "$COMMAND" in start|stop|doctor|prepare-tools|pull) acquire_launcher_lock ;; esac

case "$COMMAND" in
  build)
    [[ "$RUNTIME" == "docker" ]] || fail "The canonical application build is the Docker image; Apptainer uses the published SIF"
    exec "$ROOT_DIR/scripts/build_image.sh"
    ;;
  pull) ensure_bridge_environment; runtime_pull_application ;;
  prepare-tools) ensure_bridge_environment; ensure_token; prepare_tools ;;
  doctor)
    ensure_bridge_environment
    ensure_token
    load_launch_id
    [[ -n "$LAUNCH_ID" ]] || new_launch_id
    report_managed_toolchain
    "$BRIDGE_BIN" doctor --runtime "$RUNTIME" --data-root "$HOST_DATA_DIR" --image-dir "$IMAGE_DIR"
    BRIDGE_WAS_RUNNING=0
    if pid_matches "$BRIDGE_PID_FILE" "neurocade-runtime-bridge" && bridge_health; then BRIDGE_WAS_RUNNING=1; fi
    [[ "$BRIDGE_WAS_RUNNING" -eq 1 ]] || trap 'stop_bridge' EXIT INT TERM
    start_bridge
    bridge_health || fail "Bridge health check failed"
    echo "OK: bridge protocol $("$BRIDGE_VENV/bin/python" -c 'from neurocade_runtime_tools.protocol import PROTOCOL_VERSION; print(PROTOCOL_VERSION)'), backend $RUNTIME"
    if [[ "$BRIDGE_WAS_RUNNING" -eq 0 ]]; then
      stop_bridge
      trap - EXIT INT TERM
    fi
    ;;
  stop) stop_application; stop_bridge; rm -f "$LAUNCH_ID_FILE" ;;
  status)
    load_launch_id
    if bridge_health; then echo "Runtime bridge: running"; else echo "Runtime bridge: stopped"; fi
    if application_health; then
      if [[ -s "$APP_URL_FILE" ]]; then echo "NeuroCade: running at $(sed -n '1p' "$APP_URL_FILE")"; else echo "NeuroCade: running at http://127.0.0.1:$HTTP_PORT"; fi
    else
      echo "NeuroCade: stopped"
    fi
    ;;
  logs)
    runtime_follow_logs
    ;;
  start)
    ensure_bridge_environment
    ensure_token
    [[ "$BUILD" -eq 0 ]] || { [[ "$RUNTIME" == docker ]] || fail "--build is only valid with docker"; "$ROOT_DIR/scripts/build_image.sh"; }
    ensure_application
    runtime_prepare_database
    ensure_sample_case
    load_launch_id
    if application_health; then
      [[ -n "$LAUNCH_ID" ]] || fail "The running application has no launch-session identity; stop and restart NeuroCade"
      start_bridge
      print_browser_url "$(sed -n '1p' "$APP_URL_FILE")"
      exit 0
    fi
    begin_launch_session
    prepare_tools
    start_bridge
    rm -f "$APP_URL_FILE"
    select_http_port
    write_application_url
    if [[ "$DETACH" -eq 0 ]]; then
      # A foreground application outlives the short launcher operation. Release
      # the lock before waiting so a second terminal can stop or inspect it.
      release_launcher_lock
      trap - EXIT
    fi
    runtime_start_application
    if [[ "$DETACH" -eq 1 ]]; then wait_for_application; print_browser_url "$(sed -n '1p' "$APP_URL_FILE")"; fi
    ;;
esac
