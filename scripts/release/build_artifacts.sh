#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade release build artifacts workflow.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${RELEASE_ARTIFACT_DIR:-$ROOT_DIR/dist/release}"
VERSION="${RELEASE_VERSION:-${GITHUB_REF_NAME:-local}}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"
APPTAINER_USE_SUDO="${APPTAINER_USE_SUDO:-false}"
BASH_PYTHON_MKSQUASHFS_ARGS="${BASH_PYTHON_MKSQUASHFS_ARGS:-}"
BASH_PYTHON_BUILDFILE="$ROOT_DIR/packages/neurocade-runtime-tools/src/neurocade_runtime_tools/bash_python_image/Buildfile"
BASH_PYTHON_IMAGE_NAME="${BASH_PYTHON_IMAGE_NAME:-bash-image-python-3.12.sif}"
POSTGRES_IMAGE_NAME="${POSTGRES_IMAGE_NAME:-postgres-16-alpine.sif}"
POSTGRES_OCI="${POSTGRES_OCI:-docker://postgres:16-alpine}"
REDIS_IMAGE_NAME="${REDIS_IMAGE_NAME:-redis-7-alpine.sif}"
REDIS_OCI="${REDIS_OCI:-docker://redis:7.2.4-alpine}"
TRAEFIK_IMAGE_NAME="${TRAEFIK_IMAGE_NAME:-traefik-v2.11.14.sif}"
TRAEFIK_OCI="${TRAEFIK_OCI:-docker://traefik:v2.11.14}"
SAMPLE_CASE_ARTIFACT_NAME="${SAMPLE_CASE_ARTIFACT_NAME:-neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz}"
SAMPLE_CASE_TARBALL="${SAMPLE_CASE_TARBALL:-$ROOT_DIR/$SAMPLE_CASE_ARTIFACT_NAME}"

mkdir -p "$ARTIFACT_ROOT"

sha_file="$ARTIFACT_ROOT/SHA256SUMS.txt"
: >"$sha_file"

add_checksum() {
  local path="$1"
  (cd "$ARTIFACT_ROOT" && sha256sum "$(basename "$path")") >>"$sha_file"
}

run_apptainer() {
  if [[ "$APPTAINER_USE_SUDO" == "true" ]]; then
    local env_args=()
    if [[ -n "${APPTAINER_CACHEDIR:-}" ]]; then
      env_args+=("APPTAINER_CACHEDIR=$APPTAINER_CACHEDIR")
    fi
    if [[ -n "${APPTAINER_TMPDIR:-}" ]]; then
      env_args+=("APPTAINER_TMPDIR=$APPTAINER_TMPDIR")
    fi
    sudo "${env_args[@]}" "$APPTAINER_BIN" "$@"
  else
    "$APPTAINER_BIN" "$@"
  fi
}

restore_artifact_owner() {
  local path="$1"
  if [[ "$APPTAINER_USE_SUDO" == "true" ]]; then
    sudo chown "$(id -u):$(id -g)" "$path"
  fi
}

build_client_artifact() {
  if [[ ! -f "$ROOT_DIR/client/dist/index.html" ]]; then
    (cd "$ROOT_DIR/client" && npm ci && npm run build)
  fi
  local target="$ARTIFACT_ROOT/neurocade-client-${VERSION}.tar.gz"
  tar -C "$ROOT_DIR/client/dist" -czf "$target" .
  add_checksum "$target"
}

build_bash_python_image_artifact() {
  if ! command -v "$APPTAINER_BIN" >/dev/null 2>&1; then
    echo "Apptainer is required to build the bash Python SIF artifact." >&2
    return 1
  fi
  local target="$ARTIFACT_ROOT/$BASH_PYTHON_IMAGE_NAME"
  local build_args=(build --force)
  if [[ -n "$BASH_PYTHON_MKSQUASHFS_ARGS" ]]; then
    build_args+=(--mksquashfs-args "$BASH_PYTHON_MKSQUASHFS_ARGS")
  fi
  run_apptainer "${build_args[@]}" "$target" "$BASH_PYTHON_BUILDFILE"
  restore_artifact_owner "$target"
  add_checksum "$target"
}

build_service_image_artifact() {
  local image_name="$1"
  local oci_source="$2"
  local label="$3"
  if ! command -v "$APPTAINER_BIN" >/dev/null 2>&1; then
    echo "Apptainer is required to build the $label SIF artifact." >&2
    return 1
  fi
  local target="$ARTIFACT_ROOT/$image_name"
  run_apptainer pull --force "$target" "$oci_source"
  restore_artifact_owner "$target"
  add_checksum "$target"
}

build_service_image_artifacts() {
  build_service_image_artifact "$POSTGRES_IMAGE_NAME" "$POSTGRES_OCI" "Postgres"
  build_service_image_artifact "$REDIS_IMAGE_NAME" "$REDIS_OCI" "Redis"
  build_service_image_artifact "$TRAEFIK_IMAGE_NAME" "$TRAEFIK_OCI" "Traefik"
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
  if [[ "${SKIP_CONTAINER_ARTIFACTS:-false}" != "true" ]]; then
    build_bash_python_image_artifact
    build_service_image_artifacts
  fi
  if [[ "${SKIP_SAMPLE_CASE_ARTIFACT:-false}" != "true" ]]; then
    build_sample_case_artifact
  fi
  echo "Release artifacts written to $ARTIFACT_ROOT"
  cat "$sha_file"
}

main "$@"
