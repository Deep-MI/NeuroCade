#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer Python/uv workflow.


UV_INSTALL_URL="${NEUROCADE_UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
UV_BIN=""

find_uv() {
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

install_uv() {
  local tmp_dir installer
  tmp_dir="$(mktemp -d)"
  installer="$tmp_dir/uv-install.sh"
  trap 'rm -rf "$tmp_dir"' RETURN
  curl -fsSL "$UV_INSTALL_URL" -o "$installer"
  bash "$installer"
  trap - RETURN
  rm -rf "$tmp_dir"
}

ensure_uv() {
  UV_BIN="$(find_uv || true)"
  if [[ -n "$UV_BIN" ]]; then
    export PATH="$(dirname "$UV_BIN"):$PATH"
    return
  fi
  if [[ "${INSTALL_PREREQS:-1}" -ne 1 ]]; then
    cat >&2 <<EOF
uv is required to create NeuroCade's Python runtime, but it is not installed.

Install uv, then rerun:
  curl -LsSf $UV_INSTALL_URL | sh
  ./scripts/install.sh --mode ${MODE:-local}
EOF
    exit 1
  fi
  if [[ "${ASSUME_YES:-0}" -ne 1 ]] && is_tty && ! confirm "NeuroCade uses uv to create the Python runtime declared in pyproject.toml. Install uv now?" "y"; then
    echo "uv is required for NeuroCade installation." >&2
    exit 1
  fi
  echo "Installing uv ..."
  install_uv
  UV_BIN="$(find_uv || true)"
  if [[ -z "$UV_BIN" ]]; then
    echo "uv installation completed, but uv was not found on PATH or in the standard user install locations." >&2
    exit 1
  fi
  export PATH="$(dirname "$UV_BIN"):$PATH"
}

ensure_python_runtime() {
  local root="$1"
  ensure_uv
  if [[ ! -x "$root/.venv/bin/python" ]]; then
    "$UV_BIN" venv --project "$root" "$root/.venv"
  fi
  "$UV_BIN" pip install --python "$root/.venv/bin/python" -q \
    -r "$root/pyproject.toml"
}
