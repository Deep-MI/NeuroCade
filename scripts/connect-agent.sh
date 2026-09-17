#!/usr/bin/env bash
# One entry point for an assistant with authorized local command access.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export NEUROCADE_MCP_ENABLED=true
"$ROOT_DIR/scripts/run.sh" start --mcp -d
"$ROOT_DIR/.runtime/bridge-venv/bin/python" - "$ROOT_DIR/.env" <<'PYCODE'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text().splitlines() if path.exists() else []
lines = [line for line in lines if not line.strip().startswith("NEUROCADE_MCP_ENABLED=")]
path.write_text("\n".join([*lines, "NEUROCADE_MCP_ENABLED=true", ""]))
PYCODE
APP_URL="$(cat "$ROOT_DIR/.runtime/app-url")"
exec "$ROOT_DIR/.runtime/bridge-venv/bin/neurocade-mcp" setup --url "$APP_URL" "$@"
