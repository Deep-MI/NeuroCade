#!/usr/bin/env bash
# Build the NeuroCade monolith Docker image.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
cd "$ROOT_DIR"

source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

IMAGE="${NEUROCADE_IMAGE:-neurocade:local}"

echo "==> Building image ${IMAGE}"
docker build \
  --build-arg "NC_VITE_API_URL=${VITE_API_URL:-/api/app}" \
  --build-arg "NC_LOCAL_LOGIN=${VITE_LOCAL_AUTH_ENABLED:-${LOCAL_AUTH_ENABLED:-true}}" \
  --build-arg "NC_CLERK_PUBLIC=${VITE_CLERK_PUBLISHABLE_KEY:-}" \
  --build-arg "NC_CLERK_TEMPLATE=${VITE_CLERK_JWT_TEMPLATE:-}" \
  -f docker/backend.Dockerfile \
  -t "${IMAGE}" \
  .

echo "==> Done. Run with:"
echo "    ./scripts/run.sh"
