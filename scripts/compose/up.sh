#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

detach=0
args=()
for arg in "$@"; do
  case "$arg" in
    -d|--detach)
      detach=1
      ;;
    *)
      args+=("$arg")
      ;;
  esac
done

compose --profile images build runtime-bash-image runtime-runner api-service gateway
if [[ "$detach" -eq 1 ]]; then
  if [[ "${#args[@]}" -gt 0 ]]; then
    compose up -d "${args[@]}"
  else
    compose up -d
  fi
else
  if [[ "${#args[@]}" -gt 0 ]]; then
    compose up "${args[@]}"
  else
    compose up
  fi
fi
