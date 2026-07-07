#!/usr/bin/env bash
# Run the NeuroCade monolith with Docker only.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

CONTAINER_NAME="${NEUROCADE_CONTAINER_NAME:-neurocade}"
IMAGE="${NEUROCADE_IMAGE:-neurocade:local}"
HOST_DATA_DIR="${NEUROCADE_HOST_DATA_DIR:-${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}}"
if [[ "$HOST_DATA_DIR" != /* ]]; then
  HOST_DATA_DIR="$ROOT_DIR/$HOST_DATA_DIR"
fi
HTTP_BIND="${APP_HTTP_BIND:-127.0.0.1}"
HTTP_PORT="${APP_HTTP_PORT:-8000}"
CONTAINER_DATABASE_URL="${NEUROCADE_CONTAINER_DATABASE_URL:-sqlite+pysqlite:////data/neurocade.db}"
SAMPLE_CASE_DIR="$ROOT_DIR/sample_case"
SAMPLE_CASE_NAME="${NEUROCADE_SAMPLE_CASE_NAME:-FastSurfer_Rhineland_0000}"
SAMPLE_CASE_URL="${NEUROCADE_SAMPLE_CASE_URL:-https://github.com/Deep-MI/NeuroCade/releases/latest/download/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz}"

usage() {
  cat <<'EOF'
Usage: ./scripts/run.sh [start|stop|status|logs|build] [-d|--detach] [--build]

Default command is `start`. The script requires Docker only.
`start` builds the image when it is missing; `--build` forces a rebuild.
EOF
}

LICENSE_MOUNT_SOURCE=""
CONTAINER_FREESURFER_LICENSE=""

prepare_license_mount() {
  LICENSE_MOUNT_SOURCE=""

  if [[ -f "$HOST_DATA_DIR/license.txt" ]]; then
    CONTAINER_FREESURFER_LICENSE="/data/license.txt"
    return
  fi

  CONTAINER_FREESURFER_LICENSE="${FREESURFER_LICENSE:-}"
  if [[ -z "$CONTAINER_FREESURFER_LICENSE" ]]; then
    return
  fi

  local license_path="$CONTAINER_FREESURFER_LICENSE"
  if [[ "$license_path" != /* ]]; then
    license_path="$ROOT_DIR/$license_path"
  fi

  if [[ -f "$license_path" ]]; then
    LICENSE_MOUNT_SOURCE="$license_path"
    CONTAINER_FREESURFER_LICENSE="/fs_license.txt"
  fi
}

docker_image_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
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
  docker run --rm \
    -e "SAMPLE_CASE_URL=$SAMPLE_CASE_URL" \
    -e "SAMPLE_CASE_NAME=$SAMPLE_CASE_NAME" \
    -v "${SAMPLE_CASE_DIR}:/sample_case" \
    "$IMAGE" \
    python -c '
from pathlib import Path
import os
import sys
import tarfile
import tempfile
import urllib.request

url = os.environ["SAMPLE_CASE_URL"]
name = os.environ["SAMPLE_CASE_NAME"]
root = Path("/sample_case").resolve()
target = root / name
if target.exists() and any(target.rglob("*")):
    sys.exit(0)

with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
    urllib.request.urlretrieve(url, archive.name)
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
  start|stop|status|logs|build)
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
for arg in "$@"; do
  case "$arg" in
    -d|--detach)
      detach=1
      ;;
    --build)
      build=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$command" in
  build)
    exec "$ROOT_DIR/scripts/build_image.sh"
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
    if [[ "$build" -eq 1 ]]; then
      echo "Building image ${IMAGE} because --build was provided."
      "$ROOT_DIR/scripts/build_image.sh"
    elif ! docker_image_exists; then
      echo "Image ${IMAGE} was not found; building it before start."
      "$ROOT_DIR/scripts/build_image.sh"
    fi
    ensure_sample_case
    if docker ps --filter "name=^/${CONTAINER_NAME}$" --filter "status=running" --quiet | grep -q .; then
      echo "NeuroCade is already running: http://${HTTP_BIND}:${HTTP_PORT}"
      exit 0
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    mkdir -p "$HOST_DATA_DIR/output" "$HOST_DATA_DIR/sif"
    prepare_license_mount

    run_args=(docker run --name "$CONTAINER_NAME")
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
    )
    [[ -d "$SAMPLE_CASE_DIR" ]] && run_args+=(-v "${SAMPLE_CASE_DIR}:/app/sample_case:ro")
    [[ -n "$LICENSE_MOUNT_SOURCE" ]] && run_args+=(-v "${LICENSE_MOUNT_SOURCE}:/fs_license.txt:ro")
    [[ -f "$ENV_FILE" ]] && run_args+=(--env-file "$ENV_FILE")
    run_args+=(
      -e HOST_DATA_DIR=/data
      -e NEUROCADE_SIF_DIR=/data/sif
      -e "DATABASE_URL=${CONTAINER_DATABASE_URL}"
      -e "FREESURFER_LICENSE=${CONTAINER_FREESURFER_LICENSE}"
      -e "NEUROCADE_RUNTIME_BACKEND=${NEUROCADE_RUNTIME_BACKEND:-apptainer}"
      -e "OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
      "$IMAGE"
    )
    exec "${run_args[@]}"
    ;;
esac
