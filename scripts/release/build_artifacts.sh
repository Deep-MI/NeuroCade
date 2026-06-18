#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade release build artifacts workflow.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${RELEASE_ARTIFACT_DIR:-$ROOT_DIR/dist/release}"
VERSION="${RELEASE_VERSION:-${GITHUB_REF_NAME:-local}}"
SAMPLE_CASE_ARTIFACT_NAME="${SAMPLE_CASE_ARTIFACT_NAME:-neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz}"
SAMPLE_CASE_TARBALL="${SAMPLE_CASE_TARBALL:-$ROOT_DIR/$SAMPLE_CASE_ARTIFACT_NAME}"

mkdir -p "$ARTIFACT_ROOT"

sha_file="$ARTIFACT_ROOT/SHA256SUMS.txt"
: >"$sha_file"

add_checksum() {
  local path="$1"
  (cd "$ARTIFACT_ROOT" && sha256sum "$(basename "$path")") >>"$sha_file"
}

build_client_artifact() {
  if [[ ! -f "$ROOT_DIR/client/dist/index.html" ]]; then
    (cd "$ROOT_DIR/client" && npm ci && npm run build)
  fi
  local target="$ARTIFACT_ROOT/neurocade-client-${VERSION}.tar.gz"
  tar -C "$ROOT_DIR/client/dist" -czf "$target" .
  add_checksum "$target"
}

build_docker_release_manifest() {
  local target="$ARTIFACT_ROOT/neurocade-docker-compose-${VERSION}.txt"
  cat >"$target" <<EOF
NeuroCade Docker Compose release
Version: $VERSION

The Docker-first local install builds application images from this source tree:
  ./scripts/compose/images.sh
  ./scripts/compose/up.sh -d

Runtime services and neuroimaging tools are packaged as Docker images.
EOF
  add_checksum "$target"
}

build_sample_case_artifact() {
  if [[ ! -f "$SAMPLE_CASE_TARBALL" ]]; then
    cat >&2 <<EOF
Prebuilt sample case tarball is missing: $SAMPLE_CASE_TARBALL

Generate it before release, then rerun with:
  SAMPLE_CASE_TARBALL=/path/to/$SAMPLE_CASE_ARTIFACT_NAME ./scripts/release/build_artifacts.sh

Or set SKIP_SAMPLE_CASE_ARTIFACT=true to publish without the sample case.
EOF
    return 1
  fi

  if ! tar -tzf "$SAMPLE_CASE_TARBALL" >/dev/null; then
    echo "Sample case artifact is not a readable gzip tarball: $SAMPLE_CASE_TARBALL" >&2
    return 1
  fi

  local target="$ARTIFACT_ROOT/$SAMPLE_CASE_ARTIFACT_NAME"
  local sample_abs
  local target_abs
  sample_abs="$(cd "$(dirname "$SAMPLE_CASE_TARBALL")" && pwd)/$(basename "$SAMPLE_CASE_TARBALL")"
  target_abs="$(cd "$ARTIFACT_ROOT" && pwd)/$(basename "$target")"
  if [[ "$sample_abs" != "$target_abs" ]]; then
    cp "$SAMPLE_CASE_TARBALL" "$target"
  fi
  add_checksum "$target"
}

main() {
  build_client_artifact
  build_docker_release_manifest
  if [[ "${SKIP_SAMPLE_CASE_ARTIFACT:-false}" != "true" ]]; then
    build_sample_case_artifact
  fi
  echo "Release artifacts written to $ARTIFACT_ROOT"
  cat "$sha_file"
}

main "$@"
