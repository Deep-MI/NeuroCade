#!/usr/bin/env bash
# shellcheck shell=bash
# Background status icon (macOS menu bar / Linux AppIndicator) for detached
# launches. The icon is the Electron process client/electron/tray.mjs; this
# library decides whether to show it and owns its PID file.
#
# NEUROCADE_TRAY=auto (default) shows the icon for `start -d` in a desktop
# session when the client's Electron dependency is installed; true does the
# same but explains why it was skipped; false (or `start --no-tray`) never
# shows it. Foreground launches, including the desktop window launcher, own
# the application themselves and never start the icon.

TRAY_PID_FILE="$RUNTIME_DIR/tray.pid"
TRAY_LOG="$RUNTIME_DIR/tray.log"
TRAY_IDENTITY="electron/tray.mjs"
TRAY_MODE="${NEUROCADE_TRAY:-auto}"

tray_platform() {
  printf '%s\n' "${TRAY_PLATFORM:-$(uname -s 2>/dev/null || true)}"
}

tray_mode() {
  case "$TRAY_MODE" in
    auto|AUTO|"") echo auto ;;
    1|true|TRUE|yes|YES|on|ON) echo true ;;
    0|false|FALSE|no|NO|off|OFF) echo false ;;
    *) echo invalid ;;
  esac
}

# Print the Electron executable installed for the client, if any. Electron's
# npm package downloads its binary on first use (or via its install-electron
# command) and records the relative path in path.txt. A start never triggers
# that download itself.
tray_electron_binary() {
  local package_dir="$ROOT_DIR/client/node_modules/electron" relative
  relative="$(sed -n '1p' "$package_dir/path.txt" 2>/dev/null)" || return 1
  [[ -n "$relative" && -x "$package_dir/dist/$relative" ]] || return 1
  printf '%s\n' "$package_dir/dist/$relative"
}

# A status icon needs a graphical login session on this machine: not an SSH
# shell on macOS, and an X11/Wayland display plus a D-Bus session bus (which
# carries the StatusNotifierItem protocol) on Linux.
tray_desktop_session() {
  case "$(tray_platform)" in
    Darwin)
      [[ -z "${SSH_CONNECTION:-}${SSH_TTY:-}" ]]
      ;;
    Linux)
      [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] || return 1
      [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" || -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

# Succeed when the icon should be shown; explain a skip only if it was
# explicitly requested.
tray_should_start() {
  local mode
  mode="$(tray_mode)"
  case "$mode" in
    false) return 1 ;;
    invalid)
      echo "WARN: NEUROCADE_TRAY must be auto, true or false; not showing the status icon" >&2
      return 1
      ;;
  esac
  if ! tray_desktop_session; then
    [[ "$mode" == true ]] && echo "WARN: no desktop session; not showing the NeuroCade status icon" >&2
    return 1
  fi
  if ! tray_electron_binary >/dev/null; then
    [[ "$mode" == true ]] && echo "WARN: the status icon needs the client's Electron binary; run ./scripts/desktop/run.sh once, or 'npm --prefix client ci && npm --prefix client exec install-electron'" >&2
    return 1
  fi
}

tray_running() {
  pid_matches "$TRAY_PID_FILE" "$TRAY_IDENTITY"
}

start_tray() {
  tray_should_start || return 0
  tray_running && return 0
  rm -f "$TRAY_PID_FILE"
  local electron pid
  local -a args=()
  electron="$(tray_electron_binary)"
  # Match scripts/desktop/run.sh: Ubuntu's AppArmor policy blocks Chromium's
  # unprivileged user-namespace sandbox for binaries outside /opt.
  [[ "$(tray_platform)" == Linux ]] && args+=(--no-sandbox --disable-gpu-sandbox --disable-setuid-sandbox)
  NEUROCADE_ROOT="$ROOT_DIR" \
    NEUROCADE_RUNTIME_DIR="$RUNTIME_DIR" \
    NEUROCADE_BRIDGE_PORT="$BRIDGE_PORT" \
    nohup "$electron" ${args[@]+"${args[@]}"} "$ROOT_DIR/client/electron/tray.mjs" \
    >>"$TRAY_LOG" 2>&1 </dev/null &
  pid=$!
  disown "$pid" 2>/dev/null || true
  # /proc start times exist only on Linux; elsewhere the PID and command
  # identity still guard against a recycled PID.
  write_pid_file "$pid" "$TRAY_PID_FILE" 2>/dev/null || printf '%s\n' "$pid" >"$TRAY_PID_FILE"
  echo "Status icon: shown in the $( [[ "$(tray_platform)" == Darwin ]] && echo "menu bar" || echo "top bar" ) (disable with --no-tray or NEUROCADE_TRAY=false)"
}

stop_tray() {
  stop_pid_file "$TRAY_PID_FILE" "$TRAY_IDENTITY"
}
