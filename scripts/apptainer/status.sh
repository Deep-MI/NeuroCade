#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer status workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

for service in postgres redis host-runtime-runner api-service api-worker client update-checker traefik; do
  if is_service_running "$service"; then
    printf '%-18s running pid=%s\n' "$service" "$(cat "$(service_pid_file "$service")")"
  else
    printf '%-18s stopped\n' "$service"
  fi
done
