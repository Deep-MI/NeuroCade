#!/usr/bin/env bash
# Remove only components recorded as owned by this NeuroCade installation.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
ENV_FILE="$ROOT_DIR/.env"
INSTALL_ID_FILE="$RUNTIME_DIR/install-id"
CONFIG_FILE="$RUNTIME_DIR/installed-env"

KEEP_DATA=1
REMOVE_IMAGES=0
CONFIRMED=0

usage() {
  cat <<'EOF'
Usage: ./scripts/uninstall.sh --yes [--purge-data] [--remove-images]

Removes components proven to belong to this NeuroCade installation.
Host-installed Docker, Apptainer, Python, Node.js, and uv are never removed.
Docker images are retained unless --remove-images is specified and an image
carries this installation's ownership label.
Cases and databases are retained unless --purge-data is specified and their
ownership record matches this installation.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) CONFIRMED=1 ;;
    --purge-data) KEEP_DATA=0 ;;
    --remove-images) REMOVE_IMAGES=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ "$CONFIRMED" -eq 1 ]] || {
  echo "Refusing to uninstall without --yes." >&2
  usage >&2
  exit 2
}
[[ -f "$ROOT_DIR/scripts/install.sh" && -f "$ROOT_DIR/scripts/run.sh" ]] || {
  echo "Refusing to uninstall from an unrecognized directory: $ROOT_DIR" >&2
  exit 1
}
[[ -s "$INSTALL_ID_FILE" ]] || {
  echo "Refusing to uninstall: this checkout has no NeuroCade ownership record." >&2
  exit 1
}
[[ -s "$CONFIG_FILE" ]] || {
  echo "Refusing to uninstall: the installed configuration record is missing." >&2
  exit 1
}
INSTALL_ID="$(sed -n '1p' "$INSTALL_ID_FILE")"
[[ "$INSTALL_ID" =~ ^[0-9a-f]{32}$ ]] || {
  echo "Refusing to uninstall: the NeuroCade ownership record is invalid." >&2
  exit 1
}

source "$ROOT_DIR/scripts/lib/env.sh"
source "$ROOT_DIR/scripts/lib/docker_cli.sh"
unset NEUROCADE_RUNTIME HOST_DATA_DIR NEUROCADE_CONTAINER_NAME NEUROCADE_DATABASE_VOLUME NEUROCADE_IMAGE
ENV_FILE="$CONFIG_FILE"
load_env_file
ENV_FILE="$ROOT_DIR/.env"
configure_docker_cli_path

RUNTIME="${NEUROCADE_RUNTIME:-}"
HOST_DATA_DIR="${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
[[ "$HOST_DATA_DIR" == /* ]] || HOST_DATA_DIR="$ROOT_DIR/$HOST_DATA_DIR"
CONTAINER_NAME="${NEUROCADE_CONTAINER_NAME:-neurocade}"
DATABASE_VOLUME="${NEUROCADE_DATABASE_VOLUME:-neurocade-database}"
IMAGE="${NEUROCADE_IMAGE:-ghcr.io/deep-mi/neurocade:latest}"

case "$RUNTIME" in
  docker|apptainer) ;;
  *) echo "Refusing to uninstall: the recorded runtime is invalid." >&2; exit 1 ;;
esac
if [[ "$RUNTIME" == docker ]]; then
  [[ "$CONTAINER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] || {
    echo "Refusing to uninstall: the recorded Docker container name is invalid." >&2
    exit 1
  }
  [[ "$DATABASE_VOLUME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] || {
    echo "Refusing to uninstall: the recorded Docker volume name is invalid." >&2
    exit 1
  }
  [[ -n "$IMAGE" && "$IMAGE" != -* ]] || {
    echo "Refusing to uninstall: the recorded Docker image is invalid." >&2
    exit 1
  }
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || {
    echo "Refusing to uninstall while Docker is unavailable; its owned resources cannot be checked safely." >&2
    exit 1
  }
fi

stop_owned_pid() {
  local pid_file="$1" identity="$2" pid command_line owner_uid deadline
  [[ -f "$pid_file" ]] || return 0
  pid="$(sed -n '1p' "$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  owner_uid="$(ps -p "$pid" -o uid= 2>/dev/null | tr -d ' ')"
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$owner_uid" == "$(id -u)" && "$command_line" == *"$identity"* ]]; then
    kill -TERM "$pid" 2>/dev/null || true
    deadline=$((SECONDS + 15))
    while kill -0 "$pid" 2>/dev/null && (( SECONDS < deadline )); do sleep 1; done
    kill -KILL "$pid" 2>/dev/null || true
  else
    echo "Preserving unrecognized process recorded in $pid_file." >&2
  fi
}

if [[ "$RUNTIME" == docker ]]; then
  container_owner="$(docker inspect --format '{{index .Config.Labels "org.neurocade.install-id"}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  if [[ "$container_owner" == "$INSTALL_ID" ]]; then
    docker stop --time 15 "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
  elif docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "Preserving unowned Docker container: $CONTAINER_NAME" >&2
  fi
fi
stop_owned_pid "$RUNTIME_DIR/app.pid" "$RUNTIME_DIR/images/neurocade-app-amd64.sif"
stop_owned_pid "$RUNTIME_DIR/bridge.pid" "$RUNTIME_DIR/bridge-venv/bin/neurocade-runtime-bridge"

if [[ "$RUNTIME" == docker ]]; then
  volume_owner="$(docker volume inspect --format '{{index .Labels "org.neurocade.install-id"}}' "$DATABASE_VOLUME" 2>/dev/null || true)"
  if [[ "$KEEP_DATA" -eq 0 && "$volume_owner" == "$INSTALL_ID" ]]; then
    docker volume rm "$DATABASE_VOLUME" >/dev/null
  elif docker volume inspect "$DATABASE_VOLUME" >/dev/null 2>&1; then
    echo "Preserving Docker database volume: $DATABASE_VOLUME"
  fi
  if [[ "$REMOVE_IMAGES" -eq 1 ]]; then
    image_owner="$(docker image inspect --format '{{index .Config.Labels "org.neurocade.install-id"}}' "$IMAGE" 2>/dev/null || true)"
    if [[ "$image_owner" == "$INSTALL_ID" ]]; then
      docker image rm "$IMAGE" >/dev/null
    elif docker image inspect "$IMAGE" >/dev/null 2>&1; then
      echo "Preserving Docker image without matching ownership: $IMAGE"
    fi
  fi
fi

if [[ "$KEEP_DATA" -eq 0 && -f "$HOST_DATA_DIR/.neurocade-install-id" ]] && \
   [[ "$(sed -n '1p' "$HOST_DATA_DIR/.neurocade-install-id")" == "$INSTALL_ID" ]]; then
  rm -rf -- "$HOST_DATA_DIR"
else
  echo "Preserving data directory: $HOST_DATA_DIR"
fi

if [[ -f "$RUNTIME_DIR/electron-dependencies-owned" ]]; then
  rm -rf -- "$ROOT_DIR/client/node_modules"
fi
if [[ -f "$RUNTIME_DIR/installed-env" && -f "$ENV_FILE" ]] && cmp -s "$RUNTIME_DIR/installed-env" "$ENV_FILE"; then
  if [[ -f "$RUNTIME_DIR/preinstall-env" ]]; then
    cp "$RUNTIME_DIR/preinstall-env" "$ENV_FILE"
  else
    rm -f -- "$ENV_FILE"
  fi
elif [[ -f "$ENV_FILE" ]]; then
  echo "Preserving modified configuration: $ENV_FILE"
  if [[ -f "$RUNTIME_DIR/preinstall-env" ]]; then
    cp -n "$RUNTIME_DIR/preinstall-env" "$ROOT_DIR/.env.pre-neurocade"
    echo "Preserving pre-install configuration: $ROOT_DIR/.env.pre-neurocade"
  fi
fi

if [[ -f "$RUNTIME_DIR/runtime-root-owned" ]]; then
  if [[ "$KEEP_DATA" -eq 1 && "$RUNTIME" == apptainer && -d "$RUNTIME_DIR/database" ]]; then
    for path in "$RUNTIME_DIR"/* "$RUNTIME_DIR"/.[!.]* "$RUNTIME_DIR"/..?*; do
      [[ -e "$path" && "$path" != "$RUNTIME_DIR/database" ]] && rm -rf -- "$path"
    done
    echo "Preserving Apptainer database: $RUNTIME_DIR/database"
  else
    rm -rf -- "$RUNTIME_DIR"
  fi
else
  rm -f -- \
    "$RUNTIME_DIR/install-id" \
    "$RUNTIME_DIR/electron-dependencies-owned" \
    "$RUNTIME_DIR/installed-env" \
    "$RUNTIME_DIR/preinstall-env"
  echo "Preserving pre-existing runtime directory: $RUNTIME_DIR"
fi
echo "NeuroCade components were removed; the checkout was preserved: $ROOT_DIR"
