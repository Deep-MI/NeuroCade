#!/usr/bin/env bash
# Build the NeuroCade monolith Docker image: build the SPA on the host, then
# package it (plus the API + Apptainer) into a single image. No compose needed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
cd "$ROOT_DIR"

source "$ROOT_DIR/scripts/lib/env.sh"
load_env_file

IMAGE="${NEUROCADE_IMAGE:-neurocade:local}"

echo "==> Building frontend (client/dist)"
(
  cd client
  export VITE_API_URL="${VITE_API_URL:-/api/app}"
  export VITE_LOCAL_AUTH_ENABLED="${VITE_LOCAL_AUTH_ENABLED:-${LOCAL_AUTH_ENABLED:-true}}"
  npm ci
  npm run build
)

echo "==> Building image ${IMAGE}"
docker build -f docker/backend.Dockerfile -t "${IMAGE}" .

if [[ "${NEUROCADE_BUILD_RUNTIME_TOOLS:-1}" != "0" ]]; then
  "$ROOT_DIR/scripts/build_runtime_tools.sh"
fi

echo "==> Done. Run with:"
echo "    docker run --rm --privileged --device /dev/fuse \\"
echo "      -p 127.0.0.1:8000:8000 -v \"\$PWD/neurocade-data:/data\" --env-file .env ${IMAGE}"
