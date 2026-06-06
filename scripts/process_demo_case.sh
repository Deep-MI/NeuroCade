#!/usr/bin/env bash
# Purpose:
#   Runs the NeuroCade process case helper workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SAMPLE_CASE_DIR="$REPO_ROOT/sample_case"
RAW_T1_PATH="$SAMPLE_CASE_DIR/RLS_case_all/sub_rs_mri_raw/T1_RMS.nii.gz"
DOWNLOAD_ARGS=()
BUILD_ARGS=()
DOWNLOAD_ONLY=0
BUILD_ONLY=0

usage() {
  cat <<'EOF'
Usage: ./scripts/process_demo_case.sh [options]

One-shot demo-case builder from the repo root.

Default behavior:
  1. Download the raw Rhineland sample if it is missing
  2. Build or refresh the curated app sample case

Quick call:
  ./scripts/process_demo_case.sh

Options:
  --full-download     Download the full Rhineland bundle when a download is needed
  --download-only     Only download the sample data, do not build the sample case
  --build-only        Skip downloading and only run the sample-case builder

Build options passed through to sample_case/create_fastsurfer_sample_case.sh:
  --threads N
  --device auto|cpu|cuda
  --image IMAGE
  --reuse-generated
  --force-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full-download)
      DOWNLOAD_ARGS+=(--full)
      shift
      ;;
    --download-only)
      DOWNLOAD_ONLY=1
      shift
      ;;
    --build-only)
      BUILD_ONLY=1
      shift
      ;;
    --threads|--device|--image)
      BUILD_ARGS+=("$1" "$2")
      shift 2
      ;;
    --reuse-generated|--force-run)
      BUILD_ARGS+=("$1")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$DOWNLOAD_ONLY" -eq 1 && "$BUILD_ONLY" -eq 1 ]]; then
  echo "Error: --download-only and --build-only are mutually exclusive." >&2
  exit 1
fi

if [[ "$BUILD_ONLY" -eq 0 && ! -f "$RAW_T1_PATH" ]]; then
  echo "Raw Rhineland sample missing. Downloading it first..."
  if (( ${#DOWNLOAD_ARGS[@]} )); then
    "$SAMPLE_CASE_DIR/download_sub_rs_mri_proc.sh" "${DOWNLOAD_ARGS[@]}"
  else
    "$SAMPLE_CASE_DIR/download_sub_rs_mri_proc.sh"
  fi
elif [[ "$BUILD_ONLY" -eq 0 ]]; then
  echo "Reusing existing raw Rhineland sample at $RAW_T1_PATH"
fi

if [[ "$DOWNLOAD_ONLY" -eq 1 ]]; then
  echo "Download complete. Skipping sample-case build because --download-only was requested."
  exit 0
fi

echo "Building the app sample case..."
if (( ${#BUILD_ARGS[@]} )); then
  "$SAMPLE_CASE_DIR/create_fastsurfer_sample_case.sh" "${BUILD_ARGS[@]}"
else
  "$SAMPLE_CASE_DIR/create_fastsurfer_sample_case.sh"
fi
