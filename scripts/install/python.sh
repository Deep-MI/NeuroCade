#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer Python/uv workflow.


UV_INSTALL_URL="${NEUROCADE_UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
UV_BIN=""

uv_install_dir() {
  local root="$1"
  printf '%s\n' "$root/.runtime/uv/bin"
}

ensure_uv_state_dir() {
  local root="$1"
  local uv_state_dir="$root/.runtime/uv"

  mkdir -p "$uv_state_dir/config" "$uv_state_dir/cache"

  if [[ -z "${XDG_CONFIG_HOME:-}" || "${XDG_CONFIG_HOME:-}" != /* ]] || ! mkdir -p "$XDG_CONFIG_HOME" 2>/dev/null || [[ ! -w "$XDG_CONFIG_HOME" ]]; then
    export XDG_CONFIG_HOME="$uv_state_dir/config"
  fi
  if [[ -z "${UV_CACHE_DIR:-}" || "${UV_CACHE_DIR:-}" != /* ]] || ! mkdir -p "$UV_CACHE_DIR" 2>/dev/null || [[ ! -w "$UV_CACHE_DIR" ]]; then
    export UV_CACHE_DIR="$uv_state_dir/cache"
  fi
}

find_uv() {
  local root="${1:-}"
  local local_uv
  local_uv="$(find_local_uv "$root" || true)"
  if [[ -n "$local_uv" ]]; then
    printf '%s\n' "$local_uv"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    printf '%s\n' "$HOME/.local/bin/uv"
    return
  fi
  if [[ -x "$HOME/.cargo/bin/uv" ]]; then
    printf '%s\n' "$HOME/.cargo/bin/uv"
    return
  fi
  return 1
}

find_local_uv() {
  local root="${1:-}"
  [[ -n "$root" ]] || return 1
  if [[ -x "$(uv_install_dir "$root")/uv" ]]; then
    printf '%s\n' "$(uv_install_dir "$root")/uv"
    return
  fi
  return 1
}

install_uv() {
  local root="$1"
  local tmp_dir installer install_dir
  install_dir="$(uv_install_dir "$root")"
  mkdir -p "$install_dir"
  tmp_dir="$(mktemp -d)"
  installer="$tmp_dir/uv-install.sh"
  trap 'rm -rf "$tmp_dir"' RETURN
  curl -fsSL "$UV_INSTALL_URL" -o "$installer"
  UV_INSTALL_DIR="$install_dir" INSTALLER_NO_MODIFY_PATH=1 bash "$installer"
  trap - RETURN
  rm -rf "$tmp_dir"
}

ensure_uv() {
  local root="$1"
  UV_BIN="$(find_local_uv "$root" || true)"
  if [[ -n "$UV_BIN" ]]; then
    export PATH="$(dirname "$UV_BIN"):$PATH"
    return
  fi
  if [[ "${INSTALL_PREREQS:-1}" -ne 1 ]]; then
    UV_BIN="$(find_uv "$root" || true)"
    if [[ -n "$UV_BIN" ]]; then
      export PATH="$(dirname "$UV_BIN"):$PATH"
      return
    fi
    cat >&2 <<EOF
uv is required to create NeuroCade's Python runtime, but it is not installed.

Install uv, then rerun:
  curl -LsSf $UV_INSTALL_URL | sh
  ./scripts/install.sh --mode ${MODE:-local}
EOF
    exit 1
  fi
  if [[ "${ASSUME_YES:-0}" -ne 1 ]] && is_tty && ! confirm "NeuroCade uses a checkout-local uv to create the Python runtime declared in pyproject.toml. Install uv locally now?" "y"; then
    echo "uv is required for NeuroCade installation." >&2
    exit 1
  fi
  echo "Installing uv locally under $root/.runtime/uv ..."
  install_uv "$root"
  UV_BIN="$(find_local_uv "$root" || true)"
  if [[ -z "$UV_BIN" ]]; then
    echo "uv installation completed, but uv was not found in $root/.runtime/uv/bin." >&2
    exit 1
  fi
  export PATH="$(dirname "$UV_BIN"):$PATH"
}

ensure_python_runtime() {
  local root="$1"
  ensure_uv_state_dir "$root"
  ensure_uv "$root"
  if [[ ! -x "$root/.venv/bin/python" ]]; then
    "$UV_BIN" venv --project "$root" "$root/.venv"
  fi
  "$UV_BIN" pip install --python "$root/.venv/bin/python" -q \
    -r "$root/pyproject.toml"
}
