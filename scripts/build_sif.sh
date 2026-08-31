#!/usr/bin/env bash
# Build the application from this checkout and convert it to a local SIF.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${NEUROCADE_APP_SIF_PATH:-$ROOT_DIR/.runtime/images/neurocade-app-amd64.sif}"
SOURCE_REVISION="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || printf 'uncommitted')"
SOURCE_VERSION="source-$SOURCE_REVISION"
SOURCE_IMAGE="neurocade:$SOURCE_VERSION"
TEMPORARY_SIF="${TARGET}.building"

source "$ROOT_DIR/scripts/lib/docker_cli.sh"
configure_docker_cli_path
command -v docker >/dev/null 2>&1 || { echo "Docker is required for --build-from-source." >&2; exit 1; }
command -v apptainer >/dev/null 2>&1 || { echo "Apptainer is required to build the application SIF." >&2; exit 1; }
mkdir -p "$(dirname "$TARGET")"
rm -f "$TEMPORARY_SIF"

NEUROCADE_IMAGE="$SOURCE_IMAGE" NEUROCADE_BUILD_VERSION="$SOURCE_VERSION" "$ROOT_DIR/scripts/build_image.sh"
apptainer build --force "$TEMPORARY_SIF" "docker-daemon:$SOURCE_IMAGE"
mv "$TEMPORARY_SIF" "$TARGET"
(cd "$(dirname "$TARGET")" && sha256sum "$(basename "$TARGET")" >"$(basename "$TARGET").sha256")
printf 'source\n' >"$TARGET.mode"
echo "Built $TARGET from $SOURCE_IMAGE"
