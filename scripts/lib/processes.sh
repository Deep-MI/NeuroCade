#!/usr/bin/env bash
# Process identity and PID-file helpers shared by runtime launchers.

pid_start_time() {
  local stat
  stat="$(sed -n '1p' "/proc/$1/stat" 2>/dev/null)" || return 1
  stat="${stat##*) }"
  set -- $stat
  [[ "${20:-}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$20"
}

write_pid_file() {
  local pid="$1" pid_file="$2" start_time
  start_time="$(pid_start_time "$pid")" || return 1
  printf '%s\n%s\n' "$pid" "$start_time" >"$pid_file"
}

pid_matches() {
  local pid_file="$1" identity="$2" pid command_line owner_uid recorded_start_time current_start_time
  [[ -f "$pid_file" ]] || return 1
  pid="$(sed -n '1p' "$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  owner_uid="$(ps -p "$pid" -o uid= 2>/dev/null | tr -d ' ')"
  [[ "$owner_uid" == "$(id -u)" ]] || return 1
  recorded_start_time="$(sed -n '2p' "$pid_file")"
  if [[ -n "$recorded_start_time" ]]; then
    current_start_time="$(pid_start_time "$pid")" || return 1
    [[ "$current_start_time" == "$recorded_start_time" ]] || return 1
  fi
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"$identity"* ]] || return 1
}

stop_pid_file() {
  local pid_file="$1" identity="$2" pid deadline
  if ! pid_matches "$pid_file" "$identity"; then
    if [[ -f "$pid_file" ]]; then
      pid="$(sed -n '1p' "$pid_file")"
      if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        echo "Refusing to stop an unrecognized live process recorded in $pid_file." >&2
        return 1
      fi
    fi
    rm -f "$pid_file"
    return 0
  fi
  pid="$(sed -n '1p' "$pid_file")"
  kill -TERM "$pid" 2>/dev/null || true
  deadline=$((SECONDS + 15))
  while kill -0 "$pid" 2>/dev/null && (( SECONDS < deadline )); do sleep 1; done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$pid_file"
}
