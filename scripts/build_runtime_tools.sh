#!/usr/bin/env bash
# Build local runtime-tool artifacts used by the default Apptainer backend.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/compose/lib.sh"

normalise_arch() {
  local machine
  machine="$(uname -m 2>/dev/null || true)"
  case "$machine" in
    x86_64|amd64)
      printf 'amd64\n'
      ;;
    arm64|aarch64)
      printf 'arm64\n'
      ;;
    *)
      printf '%s\n' "$machine"
      ;;
  esac
}

echo "==> Building managed bash Docker image"
docker build -f "$ROOT_DIR/docker/runtime-bash.Dockerfile" -t neurocade-runtime-bash:local "$ROOT_DIR"

echo "==> Building managed bash Apptainer SIF"
bash_arch="$(normalise_arch)"
bash_sif_name="neurocade-runtime-bash_local-${bash_arch}.sif"
bash_archive_name="neurocade-runtime-bash_local-${bash_arch}.tar"
cleanup_archive() {
  rm -f "$NEUROCADE_SIF_DIR/$bash_archive_name"
}
trap cleanup_archive EXIT
docker save neurocade-runtime-bash:local -o "$NEUROCADE_SIF_DIR/$bash_archive_name"
docker run --rm --privileged --device /dev/fuse \
  -v "$NEUROCADE_SIF_DIR:/sif" \
  "${NEUROCADE_IMAGE:-neurocade:local}" \
  apptainer build --force "/sif/$bash_sif_name" "docker-archive:///sif/$bash_archive_name"
cleanup_archive
trap - EXIT

echo "==> Generating core tool catalog"
(
  cd "$ROOT_DIR"
  PYTHONPATH="$ROOT_DIR/packages/neurocade-runtime-tools/src" python3 -m neurocade_runtime_tools.docker_catalog
)
