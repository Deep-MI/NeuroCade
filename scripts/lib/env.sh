#!/usr/bin/env bash

quote_env_value() {
  local value="${1:-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\\$}"
  value="${value//\`/\\\`}"
  printf '"%s"' "$value"
}

decode_env_value() {
  local value="${1:-}" decoded="" character next index=0 length
  length=${#value}
  if (( length >= 2 )) && [[ "${value:0:1}" == '"' && "${value:length-1:1}" == '"' ]]; then
    value="${value:1:length-2}"
    length=${#value}
    while (( index < length )); do
      character="${value:index:1}"
      if [[ "$character" == "\\" ]] && (( index + 1 < length )); then
        next="${value:index+1:1}"
        if [[ "$next" == "\\" || "$next" == '"' || "$next" == '$' || "$next" == '`' ]]; then
          decoded+="$next"
          index=$((index + 2))
          continue
        fi
      fi
      decoded+="$character"
      index=$((index + 1))
    done
    value="$decoded"
  fi
  printf '%s\n' "$value"
}

env_line() {
  local value="${2:-}"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Environment values must fit on one line: $1" >&2
    return 1
  fi
  printf '%s=%s\n' "$1" "$(quote_env_value "$value")"
}

write_docker_env_file() {
  local source="$1" target="$2" temporary="${2}.tmp.$$" line key value
  umask 077
  : >"$temporary"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="$(decode_env_value "$value")"
    printf '%s=%s\n' "$key" "$value" >>"$temporary"
  done <"$source"
  mv "$temporary" "$target"
  chmod 600 "$target"
}

load_env_file() {
  local env_file="${ENV_FILE:-}"
  [[ -n "$env_file" && -f "$env_file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n "${!key+x}" ]] && continue
    value="$(decode_env_value "$value")"
    export "$key=$value"
  done <"$env_file"
}
