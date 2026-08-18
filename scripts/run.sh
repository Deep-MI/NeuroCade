#!/usr/bin/env bash
# Run the NeuroCade monolith with Docker only.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file
source "$ROOT_DIR/scripts/lib/doctor.sh"

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
GPU_MODE="${NEUROCADE_GPU_MODE:-auto}"
RUNTIME_UID="${NEUROCADE_UID:-$(id -u)}"
RUNTIME_GID="${NEUROCADE_GID:-$(id -g)}"
STARTUP_TIMEOUT_SECONDS="${NEUROCADE_STARTUP_TIMEOUT_SECONDS:-120}"
GPU_ARGS=()
RUNTIME_CONTAINER_ARGS=()
HOST_SYSTEM="${NEUROCADE_HOST_SYSTEM:-$(uname -s)}"
APPTAINER_TMPDIR_CONTAINER=/apptainer-tmp
APPTAINER_UNSQUASH=false
FUSE_DEVICE_ARGS=(--device /dev/fuse)
APPTAINER_TMP_MOUNT_ARGS=(-v "${HOST_DATA_DIR}/.neurocade/apptainer-tmp:/apptainer-tmp")
if [[ "$HOST_SYSTEM" == "Darwin" ]]; then
  # Docker Desktop exposes macOS bind mounts as `fakeowner`, which Apptainer
  # cannot use for OCI builds or executable FUSE mounts. Use its native Linux
  # writable layer and extraction-based SIF execution instead.
  APPTAINER_TMPDIR_CONTAINER=/tmp
  APPTAINER_UNSQUASH=true
  APPTAINER_TMP_MOUNT_ARGS=()
fi

usage() {
  cat <<'EOF'
Usage: ./scripts/run.sh [start|stop|status|logs|pull|build|prepare-tools|doctor] [options]

Default command is `start`. The script requires Docker only.
`start` pulls the published image when it is missing; `--build` builds locally.
`prepare-tools` downloads all catalog workflow images into the persistent SIF directory.
`doctor` checks the host, container runtime, storage, images, GPU, and LLM setup.

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

ensure_app_image() {
  if ! docker_image_exists; then
    echo "Image ${IMAGE} was not found; pulling it before start."
    pull_image
  elif ! docker_image_matches_platform; then
    echo "Image ${IMAGE} does not match ${DOCKER_PLATFORM}; pulling the matching image."
    pull_image
  fi
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

print_access_url() {
  local message="$1"
  local url="$2"
  if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
    printf '\033[1;32m%s \033[1;36m%s\033[0m\n' "$message" "$url"
  else
    printf '%s %s\n' "$message" "$url"
  fi
}

validate_runtime_settings() {
  case "$GPU_MODE" in
    auto|cuda|cpu) ;;
    *) echo "Invalid NEUROCADE_GPU_MODE=${GPU_MODE}. Expected auto, cuda, or cpu." >&2; exit 2 ;;
  esac
  [[ "$RUNTIME_UID" =~ ^[0-9]+$ ]] || { echo "Invalid NEUROCADE_UID=${RUNTIME_UID}." >&2; exit 2; }
  [[ "$RUNTIME_GID" =~ ^[0-9]+$ ]] || { echo "Invalid NEUROCADE_GID=${RUNTIME_GID}." >&2; exit 2; }
  [[ "$STARTUP_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
    echo "Invalid NEUROCADE_STARTUP_TIMEOUT_SECONDS=${STARTUP_TIMEOUT_SECONDS}. Expected a positive integer." >&2
    exit 2
  }
}

wait_for_backend() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if ! docker ps --filter "name=^/${CONTAINER_NAME}$" --filter "status=running" --quiet | grep -q .; then
      echo "NeuroCade exited before becoming ready." >&2
      docker logs --tail 100 "$CONTAINER_NAME" >&2 || true
      return 1
    fi
    if docker exec "$CONTAINER_NAME" python -c \
      'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/app/healthz", timeout=2).read()' \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "NeuroCade did not become ready within ${STARTUP_TIMEOUT_SECONDS} seconds." >&2
  docker logs --tail 100 "$CONTAINER_NAME" >&2 || true
  return 1
}

ensure_runtime_directories() {
  mkdir -p \
    "$HOST_DATA_DIR/output" \
    "$HOST_DATA_DIR/sif" \
    "$HOST_DATA_DIR/.neurocade/home" \
    "$HOST_DATA_DIR/.neurocade/apptainer-cache" \
    "$HOST_DATA_DIR/.neurocade/apptainer-tmp" \
    "$NEUROCADE_DB_DIR"
}

ensure_runtime_ownership() {
  local marker="$HOST_DATA_DIR/.neurocade/owner-v2-${RUNTIME_UID}-${RUNTIME_GID}"
  [[ -f "$marker" ]] && return
  echo "Preparing mounted data for UID ${RUNTIME_UID} and GID ${RUNTIME_GID}."
  docker run --rm "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    -e "TARGET_UID=$RUNTIME_UID" \
    -e "TARGET_GID=$RUNTIME_GID" \
    -v "${HOST_DATA_DIR}:/data" \
    -v "${NEUROCADE_DB_DIR}:/database" \
    --entrypoint /bin/sh \
    "$IMAGE" \
    -c '
      chown -R "${TARGET_UID}:${TARGET_GID}" /data/output /data/sif /data/.neurocade /database
      cp /etc/passwd /data/.neurocade/passwd
      if ! awk -F: -v uid="${TARGET_UID}" '\''$3 == uid { found=1 } END { exit !found }'\'' /data/.neurocade/passwd; then
        printf "neurocade-host:x:%s:%s:NeuroCade host user:/data/.neurocade/home:/bin/sh\n" "${TARGET_UID}" "${TARGET_GID}" >> /data/.neurocade/passwd
      fi
      cp /etc/group /data/.neurocade/group
      if ! awk -F: -v gid="${TARGET_GID}" '\''$3 == gid { found=1 } END { exit !found }'\'' /data/.neurocade/group; then
        printf "neurocade-host:x:%s:\n" "${TARGET_GID}" >> /data/.neurocade/group
      fi
      chmod 0644 /data/.neurocade/passwd /data/.neurocade/group
    '
  touch "$marker"
}

runtime_container_args() {
  RUNTIME_CONTAINER_ARGS=(
    --privileged
    "${FUSE_DEVICE_ARGS[@]+"${FUSE_DEVICE_ARGS[@]}"}"
    --user "${RUNTIME_UID}:${RUNTIME_GID}"
    -v "${HOST_DATA_DIR}:/data"
    -v "${NEUROCADE_DB_DIR}:/database"
    "${APPTAINER_TMP_MOUNT_ARGS[@]+"${APPTAINER_TMP_MOUNT_ARGS[@]}"}"
    -v "${HOST_DATA_DIR}/.neurocade/passwd:/etc/passwd:ro"
    -v "${HOST_DATA_DIR}/.neurocade/group:/etc/group:ro"
    -e HOME=/data/.neurocade/home
    -e APPTAINER_CACHEDIR=/data/.neurocade/apptainer-cache
    -e "APPTAINER_TMPDIR=${APPTAINER_TMPDIR_CONTAINER}"
    -e "APPTAINER_UNSQUASH=${APPTAINER_UNSQUASH}"
    -e HOST_DATA_DIR=/data
    -e NEUROCADE_SIF_DIR=/data/sif
    -e "NEUROCADE_GPU_MODE=${GPU_MODE}"
    -e DATABASE_URL=sqlite+pysqlite:////database/neurocade.db
  )
}

check_docker_apptainer() {
  if docker run --rm "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    --privileged \
    "${FUSE_DEVICE_ARGS[@]+"${FUSE_DEVICE_ARGS[@]}"}" \
    --user "${RUNTIME_UID}:${RUNTIME_GID}" \
    "${APPTAINER_TMP_MOUNT_ARGS[@]+"${APPTAINER_TMP_MOUNT_ARGS[@]}"}" \
    -e "APPTAINER_TMPDIR=${APPTAINER_TMPDIR_CONTAINER}" \
    -e "APPTAINER_UNSQUASH=${APPTAINER_UNSQUASH}" \
    --entrypoint python \
    "$IMAGE" \
    -c 'import os, tempfile; tmpdir = os.environ["APPTAINER_TMPDIR"]; unsquash = os.environ["APPTAINER_UNSQUASH"] == "true"; unsquash or os.close(os.open("/dev/fuse", os.O_RDWR)); tempfile.NamedTemporaryFile(dir=tmpdir).close()'; then
    if [[ "$APPTAINER_UNSQUASH" == "true" ]]; then
      doctor_ok "Docker supports the macOS Apptainer extraction workspace"
    else
      doctor_ok "Docker can expose /dev/fuse and the Apptainer temporary mount"
    fi
    return 0
  fi
  doctor_fail "Docker cannot provide a working Apptainer runtime workspace"
  return 1
}

prepare_tool_images() {
  runtime_container_args
  docker run --rm "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    "${GPU_ARGS[@]+"${GPU_ARGS[@]}"}" \
    "${RUNTIME_CONTAINER_ARGS[@]}" \
    "$IMAGE" \
    python -m api_service.runtime_tools.prepare_images "$@"
}

run_container_doctor() {
  runtime_container_args
  docker run --rm "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    "${GPU_ARGS[@]+"${GPU_ARGS[@]}"}" \
    "${RUNTIME_CONTAINER_ARGS[@]}" \
    "$IMAGE" \
    python -m api_service.runtime_tools.doctor "$@"
}

docker_gpu_available() {
  docker run --rm "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    --gpus all \
    --entrypoint /bin/sh \
    "$IMAGE" \
    -c 'test -c /dev/nvidiactl && command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null && ldconfig -p | grep -q "libcuda.so.1"' \
    >/dev/null 2>&1
}

configure_gpu() {
  GPU_ARGS=()
  if [[ "$GPU_MODE" == "cpu" ]]; then
    echo "GPU mode: cpu"
    return
  fi
  if docker_gpu_available; then
    GPU_ARGS=(--gpus all)
    echo "GPU mode: cuda (Docker passthrough verified)"
    return
  fi
  if [[ "$GPU_MODE" == "cuda" ]]; then
    echo "CUDA was requested, but Docker GPU passthrough is unavailable." >&2
    echo "Install/configure the NVIDIA Container Toolkit or set NEUROCADE_GPU_MODE=cpu." >&2
    exit 1
  fi
  echo "GPU mode: cpu (CUDA passthrough was not detected)"
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
    python -m api_service.runtime_tools.prepare_sample_case
}

command="${1:-start}"
case "$command" in
  start|stop|status|logs|pull|build|prepare-tools|doctor)
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
  prepare-tools)
    validate_runtime_settings
    ensure_runtime_directories
    run_host_doctor
    ensure_app_image
    check_docker_apptainer
    configure_gpu
    ensure_runtime_ownership
    run_container_doctor --pre-download
    prepare_tool_images
    ;;
  doctor)
    validate_runtime_settings
    ensure_runtime_directories
    run_host_doctor
    if docker_image_exists; then
      doctor_ok "Application image metadata is readable"
      if docker_image_matches_platform; then
        doctor_ok "Application image matches the configured platform"
      else
        doctor_fail "Application image does not match ${DOCKER_PLATFORM}"
      fi
      if check_docker_apptainer; then
        ensure_runtime_ownership
        configure_gpu
        run_container_doctor
      fi
    else
      doctor_fail "Application image $IMAGE is not installed; Apptainer and image-integrity checks cannot run"
    fi
    if (( DOCTOR_FAILURES > 0 )); then
      printf 'Doctor found %d fatal problem(s).\n' "$DOCTOR_FAILURES" >&2
      exit 1
    fi
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
    validate_runtime_settings
    ensure_runtime_directories
    run_host_doctor
    display_host="$HTTP_BIND"
    [[ "$display_host" == "0.0.0.0" || "$display_host" == "::" ]] && display_host="localhost"
    if [[ "$build" -eq 1 ]]; then
      echo "Building image ${IMAGE} because --build was provided."
      "$ROOT_DIR/scripts/build_image.sh"
    else
      ensure_app_image
    fi
    check_docker_apptainer
    ensure_sample_case
    if docker ps --filter "name=^/${CONTAINER_NAME}$" --filter "status=running" --quiet | grep -q .; then
      running_port="$HTTP_PORT"
      published_binding="$(docker port "$CONTAINER_NAME" 8000/tcp 2>/dev/null || true)"
      if [[ "$published_binding" =~ :([0-9]+)$ ]]; then
        running_port="${BASH_REMATCH[1]}"
      fi
      wait_for_backend
      print_access_url "NeuroCade is already running:" "http://${display_host}:${running_port}"
      exit 0
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    select_http_port "$HTTP_PORT"
    configure_gpu
    ensure_runtime_directories
    ensure_runtime_ownership
    run_container_doctor --pre-download
    prepare_tool_images

    run_args=(docker run --name "$CONTAINER_NAME")
    run_args+=("${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}")
    run_args+=("${GPU_ARGS[@]+"${GPU_ARGS[@]}"}")
    if [[ "$detach" -eq 1 ]]; then
      run_args+=(-d --restart unless-stopped)
    else
      run_args+=(--rm)
    fi
    runtime_container_args
    run_args+=(
      "${RUNTIME_CONTAINER_ARGS[@]}"
      --add-host host.docker.internal:host-gateway
      -p "${HTTP_BIND}:${HTTP_PORT}:8000"
    )
    [[ -d "$SAMPLE_CASE_DIR" ]] && run_args+=(-v "${SAMPLE_CASE_DIR}:/app/sample_case:ro")
    [[ -f "$ENV_FILE" ]] && run_args+=(--env-file "$ENV_FILE")
    run_args+=(
      -e "NEUROCADE_ACCESS_URL=http://${display_host}:${HTTP_PORT}"
      -e "OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
      "$IMAGE"
    )
    if [[ "$detach" -eq 1 ]]; then
      container_id="$("${run_args[@]}")"
      [[ -n "$container_id" ]] && printf '%s\n' "$container_id"
      wait_for_backend
      print_access_url "NeuroCade is ready at" "http://${display_host}:${HTTP_PORT}"
      exit 0
    fi
    print_access_url "Starting NeuroCade at" "http://${display_host}:${HTTP_PORT}"
    exec "${run_args[@]}"
    ;;
esac
