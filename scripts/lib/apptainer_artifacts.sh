#!/usr/bin/env bash
# shellcheck shell=bash

NEUROCADE_RELEASE_MANIFEST_URL="${NEUROCADE_RELEASE_MANIFEST_URL:-https://github.com/Deep-MI/NeuroCade/releases/latest/download/neurocade-release.json}"
NEUROCADE_RELEASES_API_URL="${NEUROCADE_RELEASES_API_URL:-https://api.github.com/repos/Deep-MI/NeuroCade/releases?per_page=100}"

download_release_file() {
  local url="$1" target="$2" temporary
  temporary="${target}.download"
  mkdir -p "$(dirname "$target")"
  rm -f "$temporary"
  if ! curl -fsSL --retry 3 --retry-delay 2 "$url" -o "$temporary"; then
    rm -f "$temporary"
    return 1
  fi
  mv "$temporary" "$target"
}

checksum_from_asset() {
  local checksum_file="$1" expected_name="$2" digest listed_name extra
  read -r digest listed_name extra <"$checksum_file" || return 1
  listed_name="${listed_name#\*}"
  [[ "$digest" =~ ^[0-9a-f]{64}$ && "$listed_name" == "$expected_name" && -z "${extra:-}" ]] || return 1
  printf '%s\n' "$digest"
}

verify_release_file() {
  local path="$1" expected_digest="$2" actual_digest
  actual_digest="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual_digest" == "$expected_digest" ]]
}

install_latest_apptainer_release() {
  local root="$1" python_bin="$2" selector="${3:-stable}" release_dir manifest releases
  release_dir="$root/.runtime/release"
  manifest="$release_dir/neurocade-release.json"
  releases="$release_dir/github-releases.json"
  local -a values=()
  local tag version sif_name sif_checksum_name bridge_name bridge_checksum_name release_base
  local sif_checksum bridge_checksum resolved_tag prerelease

  command -v curl >/dev/null 2>&1 || { echo "curl is required to download the NeuroCade release." >&2; return 1; }
  command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is required to verify the NeuroCade release." >&2; return 1; }
  mkdir -p "$release_dir" "$root/.runtime/images"
  if [[ "$selector" == "stable" ]] && download_release_file "$NEUROCADE_RELEASE_MANIFEST_URL" "$manifest" 2>/dev/null; then
    :
  elif [[ "$selector" =~ ^v[0-9][A-Za-z0-9._-]*$ ]]; then
    download_release_file \
      "https://github.com/Deep-MI/NeuroCade/releases/download/$selector/neurocade-release.json" "$manifest" || {
      echo "NeuroCade release $selector has no compatible Apptainer artifacts." >&2
      return 1
    }
  elif [[ "$selector" == "stable" || "$selector" == "beta" ]]; then
    if ! download_release_file "$NEUROCADE_RELEASES_API_URL" "$releases"; then
      echo "Could not query NeuroCade releases for Apptainer artifacts." >&2
      echo "Retry later or add --runtime docker to use Docker instead." >&2
      return 1
    fi
    local -a resolved=()
    while IFS= read -r value; do resolved+=("$value"); done < <(
      "$python_bin" "$root/scripts/release/release_manifest.py" resolve "$releases" --channel "$selector"
    )
    [[ "${#resolved[@]}" -eq 2 ]] || {
      echo "No compatible NeuroCade $selector release with Apptainer artifacts was found." >&2
      echo "Use --runtime docker, or rerun with --build-from-source (requires Docker)." >&2
      return 1
    }
    resolved_tag="${resolved[0]}"
    prerelease="${resolved[1]}"
    if [[ "$selector" == "stable" && "$prerelease" == "true" ]]; then
      echo "No compatible stable Apptainer release was found; using $resolved_tag instead." >&2
    fi
    download_release_file \
      "https://github.com/Deep-MI/NeuroCade/releases/download/$resolved_tag/neurocade-release.json" "$manifest" || return 1
  else
    echo "Invalid Apptainer release selector: $selector" >&2
    return 1
  fi
  while IFS= read -r value; do values+=("$value"); done < <("$python_bin" "$root/scripts/release/release_manifest.py" read "$manifest")
  [[ "${#values[@]}" -eq 6 ]] || { echo "The latest NeuroCade release manifest is invalid." >&2; return 1; }
  tag="${values[0]}"
  version="${values[1]}"
  sif_name="${values[2]}"
  sif_checksum_name="${values[3]}"
  bridge_name="${values[4]}"
  bridge_checksum_name="${values[5]}"
  release_base="https://github.com/Deep-MI/NeuroCade/releases/download/$tag"

  download_release_file "$release_base/$sif_checksum_name" "$release_dir/$sif_checksum_name" || return 1
  sif_checksum="$(checksum_from_asset "$release_dir/$sif_checksum_name" "$sif_name")" || {
    echo "The application SIF checksum asset is invalid." >&2
    return 1
  }
  download_release_file "$release_base/$bridge_checksum_name" "$release_dir/$bridge_checksum_name" || return 1
  bridge_checksum="$(checksum_from_asset "$release_dir/$bridge_checksum_name" "$bridge_name")" || {
    echo "The runtime bridge checksum asset is invalid." >&2
    return 1
  }

  echo "Downloading NeuroCade $version for Apptainer..."
  download_release_file "$release_base/$sif_name" "$release_dir/$sif_name" || return 1
  verify_release_file "$release_dir/$sif_name" "$sif_checksum" || {
    echo "Application SIF checksum verification failed." >&2
    return 1
  }
  download_release_file "$release_base/$bridge_name" "$release_dir/$bridge_name" || return 1
  verify_release_file "$release_dir/$bridge_name" "$bridge_checksum" || {
    echo "Runtime bridge checksum verification failed." >&2
    return 1
  }

  mv "$release_dir/$sif_name" "$root/.runtime/images/neurocade-app-amd64.sif"
  printf '%s  %s\n' "$sif_checksum" "neurocade-app-amd64.sif" >"$root/.runtime/images/neurocade-app-amd64.sif.sha256"
  printf 'release\n' >"$root/.runtime/images/neurocade-app-amd64.sif.mode"
  NEUROCADE_RESOLVED_BRIDGE_PACKAGE="$release_dir/$bridge_name"
  NEUROCADE_RESOLVED_RELEASE_VERSION="$version"
}
