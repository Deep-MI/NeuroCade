#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade desktop launcher run workflow.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLIENT_DIR="$ROOT_DIR/client"
if [[ -x "$ROOT_DIR/.node/bin/node" && -x "$ROOT_DIR/.node/bin/npm" ]]; then
  export PATH="$ROOT_DIR/.node/bin:$PATH"
fi
mkdir -p "$ROOT_DIR/.runtime/npm-cache"
export npm_config_cache="$ROOT_DIR/.runtime/npm-cache"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cat >&2 <<EOF
NeuroCade is not configured yet.

Run the local installer first:
  ./scripts/install.sh --mode local
EOF
  exit 1
fi

if [[ ! -x "$CLIENT_DIR/node_modules/.bin/electron" ]]; then
  echo "Installing desktop launcher dependencies..."
  (cd "$CLIENT_DIR" && npm ci)
fi

electron_args=()
case "$(uname -s 2>/dev/null || true)" in
  Linux)
    electron_args+=(--no-sandbox --disable-gpu-sandbox --disable-setuid-sandbox)
    ;;
esac

if (( ${#electron_args[@]} )); then
  exec "$CLIENT_DIR/node_modules/.bin/electron" "${electron_args[@]}" "$CLIENT_DIR/electron/main.mjs" "$@"
else
  exec "$CLIENT_DIR/node_modules/.bin/electron" "$CLIENT_DIR/electron/main.mjs" "$@"
fi
