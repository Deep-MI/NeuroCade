#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
export NEUROCADE_TIMING_OUTPUT_DIR="${NEUROCADE_TIMING_OUTPUT_DIR:-tests/screenshots/viewer-timing}"
export NEUROCADE_TIMING_SETTLE_MS="${NEUROCADE_TIMING_SETTLE_MS:-15000}"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
elif [[ -f ".env/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".env/bin/activate"
fi

echo "Running NeuroCade viewer timing analysis against ${GATEWAY_URL}"
echo "Timing artifacts will be written to ${NEUROCADE_TIMING_OUTPUT_DIR}"
echo "Post-load settle time is ${NEUROCADE_TIMING_SETTLE_MS} ms"

pytest tests/test_gui_viewer_timing.py -v
