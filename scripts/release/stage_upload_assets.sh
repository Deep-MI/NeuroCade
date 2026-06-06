#!/usr/bin/env bash
# Purpose:
#   Prepare release files for GitHub Release upload.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${RELEASE_ARTIFACT_DIR:-$ROOT_DIR/dist/release}"
UPLOAD_ROOT="${RELEASE_UPLOAD_DIR:-$ROOT_DIR/dist/release-upload}"
ASSET_LIMIT_BYTES="${GITHUB_RELEASE_ASSET_LIMIT_BYTES:-2147483648}"
SPLIT_SIZE="${RELEASE_SPLIT_SIZE:-1900M}"
SHA_FILE_NAME="SHA256SUMS.txt"
REASSEMBLE_FILE_NAME="REASSEMBLE-LARGE-ASSETS.txt"
REQUIRED_RUNTIME_ASSET_NAME="${NEUROCADE_REQUIRED_RUNTIME_ASSET_NAME:-bash-image-python-3.12.sif}"

if [[ ! -d "$ARTIFACT_ROOT" ]]; then
  echo "Release artifact directory is missing: $ARTIFACT_ROOT" >&2
  exit 1
fi

if [[ ! -f "$ARTIFACT_ROOT/$REQUIRED_RUNTIME_ASSET_NAME" ]]; then
  echo "Required runtime release asset is missing: $ARTIFACT_ROOT/$REQUIRED_RUNTIME_ASSET_NAME" >&2
  exit 1
fi
required_runtime_asset_size="$(wc -c <"$ARTIFACT_ROOT/$REQUIRED_RUNTIME_ASSET_NAME" | tr -d '[:space:]')"
if (( required_runtime_asset_size >= ASSET_LIMIT_BYTES )); then
  echo "Required runtime release asset is too large for direct GitHub upload: $ARTIFACT_ROOT/$REQUIRED_RUNTIME_ASSET_NAME" >&2
  echo "The installer expects this asset at its exact filename, not split into .part-* files." >&2
  exit 1
fi

rm -rf "$UPLOAD_ROOT"
mkdir -p "$UPLOAD_ROOT"

reassemble_file="$UPLOAD_ROOT/$REASSEMBLE_FILE_NAME"
part_count=0

file_size_bytes() {
  wc -c <"$1" | tr -d '[:space:]'
}

{
  echo "Large release assets"
  echo
  echo "GitHub Release assets must be smaller than $ASSET_LIMIT_BYTES bytes."
  echo "Any artifact above that limit is uploaded as numbered parts."
  echo
} >"$reassemble_file"

while IFS= read -r -d '' path; do
  name="$(basename "$path")"
  size="$(file_size_bytes "$path")"

  if (( size >= ASSET_LIMIT_BYTES )); then
    split -b "$SPLIT_SIZE" -d -a 3 "$path" "$UPLOAD_ROOT/$name.part-"
    {
      echo "Reassemble $name:"
      echo "  cat $name.part-* > $name"
      echo "  sha256sum -c $SHA_FILE_NAME --ignore-missing"
      echo
    } >>"$reassemble_file"
    part_count=$((part_count + 1))
  else
    cp "$path" "$UPLOAD_ROOT/$name"
  fi
done < <(find "$ARTIFACT_ROOT" -maxdepth 1 -type f ! -name "$SHA_FILE_NAME" -print0 | sort -z)

if [[ -f "$ARTIFACT_ROOT/$SHA_FILE_NAME" ]]; then
  cp "$ARTIFACT_ROOT/$SHA_FILE_NAME" "$UPLOAD_ROOT/$SHA_FILE_NAME"
else
  : >"$UPLOAD_ROOT/$SHA_FILE_NAME"
fi

if (( part_count > 0 )); then
  while IFS= read -r -d '' part; do
    (cd "$UPLOAD_ROOT" && sha256sum "$(basename "$part")") >>"$UPLOAD_ROOT/$SHA_FILE_NAME"
  done < <(find "$UPLOAD_ROOT" -maxdepth 1 -type f -name '*.part-*' -print0 | sort -z)
else
  rm -f "$reassemble_file"
fi

echo "Release upload assets staged in $UPLOAD_ROOT"
find "$UPLOAD_ROOT" -maxdepth 1 -type f -print | sort
