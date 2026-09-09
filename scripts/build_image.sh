#!/usr/bin/env bash
# Build the NeuroCade monolith Docker image.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
cd "$ROOT_DIR"

source "$ROOT_DIR/scripts/lib/env.sh"
source "$ROOT_DIR/scripts/lib/docker_cli.sh"
load_env_file
configure_docker_cli_path

IMAGE="${NEUROCADE_IMAGE:-docker.io/deepmi/neurocade:latest}"
DOCKER_PLATFORM="${NEUROCADE_DOCKER_PLATFORM:-}"
BUILD_VERSION="${NEUROCADE_BUILD_VERSION:-0.0.0}"

build_args=(docker build)
if [[ -n "$DOCKER_PLATFORM" ]]; then
  build_args+=(--platform "$DOCKER_PLATFORM")
fi

echo "==> Building image ${IMAGE}"
"${build_args[@]}" \
  -f docker/backend.Dockerfile \
  --build-arg "NEUROCADE_VERSION=${BUILD_VERSION}" \
  -t "${IMAGE}" \
  .

echo "==> Done. Run with:"
echo "    ./scripts/run.sh"
