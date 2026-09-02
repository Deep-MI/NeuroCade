#!/usr/bin/env bash
# Purpose:
#   Prepares sample case data for the NeuroCade demo workflow.


set -euo pipefail

ZENODO_RECORD_ID="19133592"
ZENODO_RECORD_URL="https://doi.org/10.5281/zenodo.19133592"
RAW_ARCHIVE="sub_rs_mri_raw.zip"
PROC_ARCHIVE="sub_rs_mri_proc.zip"
STRUC_ONLY_ARCHIVE="sub_rs_mri_struc_only.zip"
TARGET_ROOT="RLS_case_all"
FULL_DOWNLOAD=0

usage() {
  cat <<'EOF'
Usage: ./download_sub_rs_mri_proc.sh [--full]

Default:
  Download and extract only the raw Rhineland MRI example data into:
    ./RLS_case_all/sub_rs_mri_raw

Options:
  --full    Also download and extract:
            ./RLS_case_all/sub_rs_mri_proc
            ./RLS_case_all/sub_rs_mri_struc_only
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)
      FULL_DOWNLOAD=1
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

download_archive() {
  local archive="$1"
  local url="https://zenodo.org/records/${ZENODO_RECORD_ID}/files/${archive}?download=1"

  if command -v curl >/dev/null 2>&1; then
    curl -fL "$url" -o "$archive"
    return
  fi

  if command -v wget >/dev/null 2>&1; then
    wget -O "$archive" "$url"
    return
  fi

  echo "Error: neither curl nor wget is available." >&2
  exit 1
}

require_unzip() {
  if ! command -v unzip >/dev/null 2>&1; then
    echo "Error: unzip is required to unpack Zenodo archives." >&2
    exit 1
  fi
}

extract_archive() {
  local archive="$1"
  local extracted_dir="$2"
  local tmp_dir
  local target_dir="$TARGET_ROOT/$extracted_dir"

  if [[ -e "$target_dir" ]]; then
    echo "Reusing existing $target_dir"
    return
  fi

  tmp_dir="$(mktemp -d)"

  echo "Unpacking $archive..."
  unzip -oq "$archive" -d "$tmp_dir"

  if [[ ! -d "$tmp_dir/$extracted_dir" ]]; then
    rm -rf "$tmp_dir"
    echo "Error: expected $extracted_dir inside $archive, but it was not found." >&2
    exit 1
  fi

  mkdir -p "$TARGET_ROOT"
  mv "$tmp_dir/$extracted_dir" "$target_dir"
  rm -rf "$tmp_dir"
}

ensure_archive() {
  local archive="$1"

  if [[ -f "$archive" ]]; then
    echo "Reusing existing $archive"
    return
  fi

  echo "Downloading $archive from $ZENODO_RECORD_URL..."
  download_archive "$archive"
}

cd "$(dirname "$0")"

require_unzip
ensure_archive "$RAW_ARCHIVE"
extract_archive "$RAW_ARCHIVE" "sub_rs_mri_raw"

if [[ "$FULL_DOWNLOAD" -eq 1 ]]; then
  ensure_archive "$PROC_ARCHIVE"
  ensure_archive "$STRUC_ONLY_ARCHIVE"
  extract_archive "$PROC_ARCHIVE" "sub_rs_mri_proc"
  extract_archive "$STRUC_ONLY_ARCHIVE" "sub_rs_mri_struc_only"
fi

echo "Done."
echo "  Downloaded data: $(pwd)/$TARGET_ROOT/sub_rs_mri_raw"
if [[ "$FULL_DOWNLOAD" -eq 1 ]]; then
  echo "  Full dataset:    $(pwd)/$TARGET_ROOT"
fi
echo "  Next step:       inspect the raw MRI files or use the release sample-case artifact"
