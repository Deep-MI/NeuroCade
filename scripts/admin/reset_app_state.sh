#!/usr/bin/env bash
# Purpose:
#   Runs the NeuroCade reset app state helper workflow.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

KEEP_STACK_DOWN=0
CONFIRMED=0

for arg in "$@"; do
  case "$arg" in
    --yes)
      CONFIRMED=1
      ;;
    --keep-stack-down)
      KEEP_STACK_DOWN=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 --yes [--keep-stack-down]" >&2
      exit 2
      ;;
  esac
done

if [[ "$CONFIRMED" -ne 1 ]]; then
  echo "Refusing to run without --yes." >&2
  echo "This command wipes the SQLite database and workspace data." >&2
  exit 2
fi

canonical_path() {
  local path="$1" name resolved suffix=""
  [[ "$path" == /* ]] || path="$PWD/$path"

  # GNU realpath supports -m for missing paths; macOS realpath does not. Resolve
  # the deepest existing ancestor and append safe missing components instead.
  while [[ ! -e "$path" ]]; do
    name="${path##*/}"
    case "$name" in
      ""|.|..)
        echo "Cannot safely resolve path: $1" >&2
        return 1
        ;;
    esac
    suffix="/$name$suffix"
    path="${path%/*}"
    [[ -n "$path" ]] || path="/"
  done
  resolved="$(realpath "$path")"
  printf '%s%s\n' "$resolved" "$suffix"
}

require_repo_local_path() {
  local label="$1"
  local path="$2"
  local root_real path_real
  root_real="$(canonical_path "$ROOT_DIR")"
  path_real="$(canonical_path "$path")"
  case "$path_real" in
    "$root_real"|"$root_real"/*)
      ;;
    *)
      echo "Refusing to reset $label outside the repository: $path_real" >&2
      exit 1
      ;;
  esac
}

HOST_DATA_DIR="${HOST_DATA_DIR:-$ROOT_DIR/neurocade-data}"
RUNTIME_DIR="${NEUROCADE_RUNTIME_DIR:-$ROOT_DIR/.runtime}"
DATABASE_VOLUME="${NEUROCADE_DATABASE_VOLUME:-neurocade-database}"
APPTAINER_DATABASE_DIR="$RUNTIME_DIR/database"

require_repo_local_path ".runtime" "$RUNTIME_DIR"
require_repo_local_path "HOST_DATA_DIR" "$HOST_DATA_DIR"

kill_repo_service_orphans() {
  local pids pid
  pids="$(pgrep -u "$(id -u)" -f "$ROOT_DIR/.venv/bin/python|api_service.main:app|neurocade-runtime-bridge" || true)"
  [[ -n "$pids" ]] || return 0
  for pid in $pids; do
    [[ "$pid" != "$$" ]] || continue
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  for pid in $pids; do
    [[ "$pid" != "$$" ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    kill -9 "$pid" 2>/dev/null || true
  done
}

echo "Stopping NeuroCade container..."
"$ROOT_DIR/scripts/run.sh" stop
kill_repo_service_orphans

echo "Removing local runtime state..."
rm -rf "$RUNTIME_DIR/pids" "$RUNTIME_DIR/logs"
rm -f "$RUNTIME_DIR/app.log" "$RUNTIME_DIR/bridge.log" "$RUNTIME_DIR/app.pid" "$RUNTIME_DIR/bridge.pid"
mkdir -p "$RUNTIME_DIR/pids" "$RUNTIME_DIR/logs"

echo "Wiping workspace and database state under $HOST_DATA_DIR..."
rm -rf "$HOST_DATA_DIR/output"
mkdir -p "$HOST_DATA_DIR/output"
if [[ "${NEUROCADE_RUNTIME:-}" == "docker" ]]; then
  echo "Removing SQLite volume $DATABASE_VOLUME..."
  docker volume rm "$DATABASE_VOLUME" >/dev/null 2>&1 || true
else
  echo "Removing SQLite state from $APPTAINER_DATABASE_DIR..."
  rm -f \
    "$APPTAINER_DATABASE_DIR/neurocade.db" \
    "$APPTAINER_DATABASE_DIR/neurocade.db-shm" \
    "$APPTAINER_DATABASE_DIR/neurocade.db-wal"
fi

if [[ "$KEEP_STACK_DOWN" -eq 1 ]]; then
  echo "Reset complete. Stack left stopped."
  exit 0
fi

echo "Starting NeuroCade..."
"$ROOT_DIR/scripts/run.sh" start -d

echo "Reset complete."
