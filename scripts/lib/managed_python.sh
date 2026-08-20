#!/usr/bin/env bash

NEUROCADE_UV_VERSION="0.8.17"
NEUROCADE_PYTHON_VERSION="3.12"
MANAGED_UV_BIN="$ROOT_DIR/.runtime/uv-bin/uv"
MANAGED_PYTHON_DIR="$ROOT_DIR/.runtime/python"
export UV_PYTHON_INSTALL_DIR="$MANAGED_PYTHON_DIR"

neurocade_process_arch() {
  uname -m
}

neurocade_is_rosetta() {
  [[ "$(uname -s)" == "Darwin" && "$(neurocade_process_arch)" == "x86_64" ]] || return 1
  command -v sysctl >/dev/null 2>&1 || return 1
  [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" == "1" ]]
}

neurocade_host_arch() {
  if neurocade_is_rosetta; then
    printf 'arm64\n'
  else
    neurocade_process_arch
  fi
}

managed_uv_matches_host_arch() {
  [[ "$(uname -s)" == "Darwin" && "$(neurocade_host_arch)" == "arm64" ]] || return 0
  command -v file >/dev/null 2>&1 || return 0
  file "$MANAGED_UV_BIN" 2>/dev/null | grep -q 'arm64'
}

managed_uv_version_matches() {
  local version_output
  [[ -x "$MANAGED_UV_BIN" ]] || return 1
  version_output="$("$MANAGED_UV_BIN" --version 2>/dev/null)" || return 1
  case "$version_output" in
    "uv $NEUROCADE_UV_VERSION"|"uv $NEUROCADE_UV_VERSION "*) ;;
    *) return 1 ;;
  esac
  managed_uv_matches_host_arch
}

require_managed_uv() {
  managed_uv_version_matches || {
    echo "Managed uv $NEUROCADE_UV_VERSION is missing; rerun scripts/install.sh." >&2
    return 1
  }
}

managed_uv() {
  require_managed_uv || return 1
  "$MANAGED_UV_BIN" "$@"
}

install_managed_uv() {
  local install_dir
  managed_uv_version_matches && return
  command -v curl >/dev/null 2>&1 || {
    echo "curl is required to install uv." >&2
    return 1
  }
  install_dir="$(dirname "$MANAGED_UV_BIN")"
  mkdir -p "$install_dir"
  echo "Installing managed uv $NEUROCADE_UV_VERSION..."
  if neurocade_is_rosetta; then
    curl -LsSf "https://astral.sh/uv/$NEUROCADE_UV_VERSION/install.sh" |
      env \
        PATH="$install_dir:$PATH" \
        UV_INSTALL_DIR="$install_dir" \
        UV_NO_MODIFY_PATH=1 \
        /usr/bin/arch -arm64 /bin/sh
  else
    curl -LsSf "https://astral.sh/uv/$NEUROCADE_UV_VERSION/install.sh" |
      env PATH="$install_dir:$PATH" UV_INSTALL_DIR="$install_dir" UV_NO_MODIFY_PATH=1 sh
  fi
  require_managed_uv
}

managed_python_path() {
  managed_uv python find --managed-python "$NEUROCADE_PYTHON_VERSION"
}
