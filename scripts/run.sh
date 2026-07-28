#!/usr/bin/env bash
# Run the NeuroCade monolith with Docker only.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

CONTAINER_NAME="${NEUROCADE_CONTAINER_NAME:-neurocade}"
IMAGE="${NEUROCADE_IMAGE:-ghcr.io/deep-mi/neurocade:latest}"
DOCKER_PLATFORM="${NEUROCADE_DOCKER_PLATFORM:-}"
PLATFORM_ARGS=()
[[ -n "$DOCKER_PLATFORM" ]] && PLATFORM_ARGS+=(--platform "$DOCKER_PLATFORM")
HOST_DATA_DIR="${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
if [[ "$HOST_DATA_DIR" != /* ]]; then
  HOST_DATA_DIR="$ROOT_DIR/$HOST_DATA_DIR"
fi
NEUROCADE_DB_DIR="${NEUROCADE_DB_DIR:-$HOST_DATA_DIR}"
if [[ "$NEUROCADE_DB_DIR" != /* ]]; then
  NEUROCADE_DB_DIR="$ROOT_DIR/$NEUROCADE_DB_DIR"
fi
HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
HTTP_PORT="${APP_HTTP_PORT:-8000}"
SAMPLE_CASE_DIR="$ROOT_DIR/sample_case"
SAMPLE_CASE_NAME="${NEUROCADE_SAMPLE_CASE_NAME:-FastSurfer_Rhineland_0000}"
SAMPLE_CASE_URL="${NEUROCADE_SAMPLE_CASE_URL:-https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.7/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz}"
SAMPLE_CASE_SHA256="${NEUROCADE_SAMPLE_CASE_SHA256:-71814b4687180e10543523bb07292725f4b165acce7d0f9d34148028daa061b7}"

usage() {
  cat <<'EOF'
Usage: ./scripts/run.sh [start|stop|status|logs|pull|build] [options]

Default command is `start`. The script requires Docker only.
`start` pulls the published image when it is missing; `--build` builds locally.

Start options:
  -d, --detach       Run the container in the background.
  --build            Build the image locally before starting.
  --port PORT        Prefer this host port (default: APP_HTTP_PORT or 8000).
                     If it is occupied, the next available port is used.
EOF
}

docker_image_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

docker_image_matches_platform() {
  [[ -z "$DOCKER_PLATFORM" ]] && return 0
  [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE" 2>/dev/null)" == "$DOCKER_PLATFORM" ]]
}

pull_image() {
  echo "==> Pulling image ${IMAGE}"
  docker pull "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" "$IMAGE"
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltn "sport = :${port}" 2>/dev/null | grep -q .
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  return 1
}

select_http_port() {
  local preferred_port="$1"
  HTTP_PORT="$preferred_port"
  while port_in_use "$HTTP_PORT"; do
    if [[ "$HTTP_PORT" -eq 65535 ]]; then
      echo "No available TCP port found at or above ${preferred_port}." >&2
      exit 1
    fi
    HTTP_PORT=$((HTTP_PORT + 1))
  done
  if [[ "$HTTP_PORT" -ne "$preferred_port" ]]; then
    echo "Port ${preferred_port} is already in use; using port ${HTTP_PORT} instead."
  fi
}

sample_case_installed() {
  [[ -d "$SAMPLE_CASE_DIR/$SAMPLE_CASE_NAME" ]] && find "$SAMPLE_CASE_DIR/$SAMPLE_CASE_NAME" -type f -print -quit | grep -q .
}

ensure_sample_case() {
  truthy "${NEUROCADE_SKIP_SAMPLE_CASE:-false}" && return
  [[ -n "$SAMPLE_CASE_URL" ]] || return
  sample_case_installed && return

  echo "Sample case ${SAMPLE_CASE_NAME} was not found; downloading it before start."
  mkdir -p "$SAMPLE_CASE_DIR"
  docker run --rm "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    -e "SAMPLE_CASE_URL=$SAMPLE_CASE_URL" \
    -e "SAMPLE_CASE_NAME=$SAMPLE_CASE_NAME" \
    -e "SAMPLE_CASE_SHA256=$SAMPLE_CASE_SHA256" \
    -v "${SAMPLE_CASE_DIR}:/sample_case" \
    "$IMAGE" \
    python -c '
from pathlib import Path
import hashlib
import os
import sys
import tarfile
import tempfile
import urllib.request

url = os.environ["SAMPLE_CASE_URL"]
name = os.environ["SAMPLE_CASE_NAME"]
expected_sha256 = os.environ["SAMPLE_CASE_SHA256"]
root = Path("/sample_case").resolve()
target = root / name
if target.exists() and any(target.rglob("*")):
    sys.exit(0)

with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
    urllib.request.urlretrieve(url, archive.name)
    with open(archive.name, "rb") as sample_file:
        digest = hashlib.file_digest(sample_file, "sha256").hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"Sample archive checksum mismatch: expected {expected_sha256}, got {digest}")
    with tarfile.open(archive.name, "r:gz") as tar:
        for member in tar.getmembers():
            destination = (root / member.name).resolve()
            if root != destination and root not in destination.parents:
                raise RuntimeError(f"Unsafe sample archive path: {member.name}")
        tar.extractall(root)

if not target.exists() or not any(target.rglob("*")):
    raise RuntimeError(f"Sample archive did not create {target}")
'
}

command="${1:-start}"
case "$command" in
  start|stop|status|logs|pull|build)
    shift || true
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    command="start"
    ;;
esac

detach=0
build=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -d|--detach)
      detach=1
      shift
      ;;
    --build)
      build=1
      shift
      ;;
    --port)
      if [[ "$#" -lt 2 || "$2" == -* ]]; then
        echo "--port requires a value." >&2
        usage >&2
        exit 2
      fi
      HTTP_PORT="$2"
      shift 2
      ;;
    --port=*)
      HTTP_PORT="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$HTTP_PORT" =~ ^[0-9]+$ ]] || (( 10#$HTTP_PORT < 1 || 10#$HTTP_PORT > 65535 )); then
  echo "Invalid port: ${HTTP_PORT}. Expected an integer from 1 to 65535." >&2
  exit 2
fi
HTTP_PORT=$((10#$HTTP_PORT))

case "$command" in
  build)
    exec "$ROOT_DIR/scripts/build_image.sh"
    ;;
  pull)
    pull_image
    ;;
  stop)
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    ;;
  status)
    docker ps -a --filter "name=^/${CONTAINER_NAME}$"
    ;;
  logs)
    exec docker logs -f "$CONTAINER_NAME"
    ;;
  start)
    display_host="$HTTP_BIND"
    [[ "$display_host" == "0.0.0.0" || "$display_host" == "::" ]] && display_host="localhost"
    if [[ "$build" -eq 1 ]]; then
      echo "Building image ${IMAGE} because --build was provided."
      "$ROOT_DIR/scripts/build_image.sh"
    elif ! docker_image_exists; then
      echo "Image ${IMAGE} was not found; pulling it before start."
      pull_image
    elif ! docker_image_matches_platform; then
      echo "Image ${IMAGE} does not match ${DOCKER_PLATFORM}; pulling the matching image."
      pull_image
    fi
    ensure_sample_case
    if docker ps --filter "name=^/${CONTAINER_NAME}$" --filter "status=running" --quiet | grep -q .; then
      running_port="$HTTP_PORT"
      published_binding="$(docker port "$CONTAINER_NAME" 8000/tcp 2>/dev/null || true)"
      if [[ "$published_binding" =~ :([0-9]+)$ ]]; then
        running_port="${BASH_REMATCH[1]}"
      fi
      echo "NeuroCade is already running: http://${display_host}:${running_port}"
      exit 0
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    select_http_port "$HTTP_PORT"
    mkdir -p "$HOST_DATA_DIR/output" "$HOST_DATA_DIR/sif" "$NEUROCADE_DB_DIR"

    echo "Starting NeuroCade at http://${display_host}:${HTTP_PORT}"

    run_args=(docker run --name "$CONTAINER_NAME")
    run_args+=("${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}")
    if [[ "$detach" -eq 1 ]]; then
      run_args+=(-d --restart unless-stopped)
    else
      run_args+=(--rm)
    fi
    run_args+=(
      --privileged
      --device /dev/fuse
      --add-host host.docker.internal:host-gateway
      -p "${HTTP_BIND}:${HTTP_PORT}:8000"
      -v "${HOST_DATA_DIR}:/data"
      -v "${NEUROCADE_DB_DIR}:/database"
    )
    [[ -d "$SAMPLE_CASE_DIR" ]] && run_args+=(-v "${SAMPLE_CASE_DIR}:/app/sample_case:ro")
    [[ -f "$ENV_FILE" ]] && run_args+=(--env-file "$ENV_FILE")
    run_args+=(
      -e HOST_DATA_DIR=/data
      -e NEUROCADE_SIF_DIR=/data/sif
      -e DATABASE_URL=sqlite+pysqlite:////database/neurocade.db
      -e "NEUROCADE_RUNTIME_BACKEND=${NEUROCADE_RUNTIME_BACKEND:-apptainer}"
      -e "OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
      "$IMAGE"
    )
    exec "${run_args[@]}"
    ;;
esac
