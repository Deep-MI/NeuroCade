#!/usr/bin/env bash
# Purpose:
#   Runs the NeuroCade containers helper workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  cat >&2 <<EOF
NeuroCade runtime container tooling requires the project virtualenv at:
  $ROOT_DIR/.venv

Create it with uv, then rerun:
  uv venv --project "$ROOT_DIR" "$ROOT_DIR/.venv"
  uv pip install --python "$ROOT_DIR/.venv/bin/python" -r "$ROOT_DIR/pyproject.toml"
EOF
  exit 1
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT_DIR/packages/neurocade-runtime-tools/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m neurocade_runtime_tools.containers "$@"
