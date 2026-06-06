#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer images workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'EOF'
Usage: ./scripts/apptainer/images.sh [infra|all|preflight]

Fetch rootless Apptainer infrastructure SIF images. Runtime/tool containers are
managed by ./scripts/containers.sh.
The script never uses sudo.
EOF
}

download_or_pull() {
  local target="$1"
  local url="$2"
  local oci="$3"
  local label="$4"
  if [[ -f "$target" ]]; then
    echo "$label image exists: $target"
    return 0
  fi
  mkdir -p "$(dirname "$target")"
  if [[ -n "$url" ]]; then
    echo "Downloading $label image from $url"
    if curl -fL "$url" -o "$target"; then
      return 0
    fi
    rm -f "$target"
    if [[ -z "$oci" ]]; then
      return 1
    fi
    echo "Download failed; falling back to $oci"
  fi
  if [[ -n "$oci" ]]; then
    echo "Pulling $label image from $oci"
    "$APPTAINER_BIN" pull --force "$target" "$oci"
    return 0
  fi
  echo "No URL or OCI source configured for $label ($target)" >&2
  return 1
}

preflight() {
  require_apptainer
  "$APPTAINER_BIN" --version
  if [[ -r /proc/sys/kernel/unprivileged_userns_clone ]]; then
    echo "unprivileged_userns_clone=$(cat /proc/sys/kernel/unprivileged_userns_clone)"
  fi
  local tmp
  tmp="$(mktemp -d /tmp/neurocade-apptainer.XXXXXX)"
  "$APPTAINER_BIN" exec --bind "$tmp:/mnt:rw" docker://alpine sh -lc 'echo ok > /mnt/probe.txt'
  if [[ "$(cat "$tmp/probe.txt")" != "ok" ]]; then
    echo "Apptainer writable bind probe failed" >&2
    return 1
  fi
  echo "Apptainer runtime preflight passed."
  if "$APPTAINER_BIN" exec --fakeroot docker://alpine true >/dev/null 2>&1; then
    echo "fakeroot preflight passed; local package-installing builds may work."
  else
    echo "fakeroot preflight failed; use release SIF URLs or OCI pulls, not local package-installing builds."
  fi
}

infra() {
  require_apptainer
  download_or_pull "$POSTGRES_SIF" "${POSTGRES_SIF_URL:-}" "$POSTGRES_OCI" "Postgres"
  download_or_pull "$REDIS_SIF" "${REDIS_SIF_URL:-}" "$REDIS_OCI" "Redis"
  download_or_pull "$TRAEFIK_SIF" "${TRAEFIK_SIF_URL:-}" "$TRAEFIK_OCI" "Traefik"
}

case "${1:-all}" in
  preflight)
    preflight
    ;;
  infra|all)
    infra
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
