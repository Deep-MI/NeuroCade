#!/usr/bin/env bash
# Build the NeuroCade monolith Docker image.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
cd "$ROOT_DIR"

source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

IMAGE="${NEUROCADE_IMAGE:-ghcr.io/deep-mi/neurocade:latest}"
DOCKER_PLATFORM="${NEUROCADE_DOCKER_PLATFORM:-}"

build_args=(docker build)
if [[ -n "$DOCKER_PLATFORM" ]]; then
  build_args+=(--platform "$DOCKER_PLATFORM")
fi

echo "==> Building image ${IMAGE}"
"${build_args[@]}" \
  -f docker/backend.Dockerfile \
  -t "${IMAGE}" \
  .

echo "==> Done. Run with:"
echo "    ./scripts/run.sh"
