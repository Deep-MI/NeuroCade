#!/usr/bin/env bash
# Purpose:
#   Runs the NeuroCade reset app state helper workflow.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/install/env.sh"

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
  echo "This command drops the application schema, flushes Redis, and wipes workspace data under HOST_DATA_DIR." >&2
  exit 2
fi

require_repo_local_path() {
  local label="$1"
  local path="$2"
  local root_real path_real
  root_real="$(realpath -m "$ROOT_DIR")"
  path_real="$(realpath -m "$path")"
  case "$path_real" in
    "$root_real"|"$root_real"/*)
      ;;
    *)
      echo "Refusing to reset $label outside the repository: $path_real" >&2
      exit 1
      ;;
  esac
}

HOST_DATA_DIR="$(env_config_value "$ROOT_DIR" HOST_DATA_DIR "$ROOT_DIR/neurocade-data")"
RUNTIME_DIR="$(env_config_value "$ROOT_DIR" NEUROCADE_RUNTIME_DIR "$ROOT_DIR/.runtime")"

require_repo_local_path ".runtime" "$RUNTIME_DIR"
require_repo_local_path "HOST_DATA_DIR" "$HOST_DATA_DIR"

source "$ROOT_DIR/scripts/apptainer/lib.sh"

kill_repo_service_orphans() {
  local pids pid
  pids="$(pgrep -u "$(id -u)" -f "$ROOT_DIR/.venv/bin/python|$ROOT_DIR/scripts/serve_static_client.py|api_service.main:app|api_service.host_runtime_runner:app|api_service.celery_app|redis-server ${REDIS_HOST}:${REDIS_PORT}|postgres -D /var/lib/postgresql/data -h ${POSTGRES_HOST} -p ${POSTGRES_PORT}" || true)"
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

echo "Stopping Apptainer-managed services..."
"$ROOT_DIR/scripts/apptainer/down.sh"
kill_repo_service_orphans

echo "Removing local runtime state..."
rm -rf "$RUNTIME_DIR/postgres" "$RUNTIME_DIR/redis" "$RUNTIME_DIR/pids" "$RUNTIME_DIR/logs"
mkdir -p "$RUNTIME_DIR/postgres" "$RUNTIME_DIR/redis" "$RUNTIME_DIR/pids" "$RUNTIME_DIR/logs"

echo "Wiping $HOST_DATA_DIR contents except license.txt..."
find "$HOST_DATA_DIR" -mindepth 1 -maxdepth 1 ! -name 'license.txt' -exec rm -rf {} +
mkdir -p "$HOST_DATA_DIR/output"

if [[ "$KEEP_STACK_DOWN" -eq 1 ]]; then
  echo "Reset complete. Stack left stopped."
  exit 0
fi

echo "Starting the stack..."
"$ROOT_DIR/scripts/apptainer/up.sh" -d

echo "Reset complete."
