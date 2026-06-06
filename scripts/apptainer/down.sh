#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer down workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

for service in traefik update-checker client api-worker api-service host-runtime-runner redis postgres; do
  stop_service "$service"
done
