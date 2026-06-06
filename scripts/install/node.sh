#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer node workflow.


prepend_local_node() {
  local root="$1"
  local node_bin="$root/$LOCAL_NODE_DIR_REL/bin"
  if [[ -x "$node_bin/node" && -x "$node_bin/npm" ]]; then
    export PATH="$node_bin:$PATH"
  fi
}

node_major_version() {
  node -v 2>/dev/null | sed -n 's/^v\([0-9][0-9]*\).*/\1/p'
}

ensure_node() {
  local root="$1"
  prepend_local_node "$root"
  local major
  major="$(node_major_version || true)"
  if [[ -n "$major" && "$major" -ge 20 ]] && command -v npm >/dev/null 2>&1; then
    log_step "Node.js $(node -v) and npm $(npm -v) available"
    return 0
  fi
  [[ "${INSTALL_PREREQS:-1}" -eq 1 ]] || {
    echo "Node.js 20+ and npm are required. Re-run without --no-prereqs to install a repo-local Node.js runtime." >&2
    exit 1
  }

  local uname_s uname_m platform arch ext filename url sums_url archive node_dir tmp_dir
  uname_s="$(uname -s 2>/dev/null || true)"
  uname_m="$(uname -m 2>/dev/null || true)"
  case "$uname_s:$uname_m" in
    Darwin:x86_64) platform="darwin"; arch="x64"; ext="tar.gz" ;;
    Darwin:arm64) platform="darwin"; arch="arm64"; ext="tar.gz" ;;
    Linux:x86_64|Linux:amd64) platform="linux"; arch="x64"; ext="tar.xz" ;;
    Linux:aarch64|Linux:arm64) platform="linux"; arch="arm64"; ext="tar.xz" ;;
    *)
      echo "Automatic local Node.js install is not supported on ${uname_s:-unknown}/${uname_m:-unknown}." >&2
      echo "Install Node.js 20+ and npm 10+, then rerun $SCRIPT_NAME." >&2
      exit 1
      ;;
  esac

  echo "Installing Node.js locally under $root/$LOCAL_NODE_DIR_REL ..."
  sums_url="https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt"
  filename="$(curl -fsSL "$sums_url" | awk -v platform="$platform" -v arch="$arch" -v ext="$ext" '$2 ~ "^node-v[0-9.]+-" platform "-" arch "\\." ext "$" { print $2; exit }')"
  if [[ -z "$filename" ]]; then
    echo "Could not resolve a Node.js v22 binary for $platform-$arch." >&2
    exit 1
  fi
  url="https://nodejs.org/dist/latest-v22.x/$filename"
  tmp_dir="$(mktemp -d)"
  archive="$tmp_dir/$filename"
  download_with_sha256 "$url" "$sums_url" "$filename" "$archive"
  node_dir="$root/$LOCAL_NODE_DIR_REL"
  rm -rf "$node_dir"
  mkdir -p "$node_dir"
  tar -xf "$archive" -C "$node_dir" --strip-components=1
  rm -rf "$tmp_dir"
  prepend_local_node "$root"
  echo "Node.js installed: $("$node_dir/bin/node" -v), npm $("$node_dir/bin/npm" -v)"
}

ensure_desktop_prerequisites() {
  [[ "${INSTALL_PREREQS:-1}" -eq 1 ]] || return 0
  ensure_node "$1"
}

client_dependencies_current() {
  local root="$1"
  local client_dir="$root/client"
  local installed_lock="$client_dir/node_modules/.package-lock.json"

  [[ -x "$client_dir/node_modules/.bin/electron" ]] || return 1
  [[ -f "$client_dir/package.json" ]] || return 1
  [[ -f "$client_dir/package-lock.json" ]] || return 1
  [[ -f "$installed_lock" ]] || return 1
  [[ ! "$client_dir/package.json" -nt "$installed_lock" ]] || return 1
  [[ ! "$client_dir/package-lock.json" -nt "$installed_lock" ]] || return 1
}
