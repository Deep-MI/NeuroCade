#!/usr/bin/env bash
# Purpose:
#   Keeps Node.js, npm, and Electron cache state inside the NeuroCade install.

configure_node_runtime_cache() {
  local root="$1"
  local runtime_dir="${2:-$root/.runtime}"

  export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-$runtime_dir/npm-cache}"
  export ELECTRON_CACHE="${ELECTRON_CACHE:-$runtime_dir/electron/download-cache}"
  export electron_config_cache="${electron_config_cache:-$ELECTRON_CACHE}"

  mkdir -p "$NPM_CONFIG_CACHE" "$ELECTRON_CACHE"
}
