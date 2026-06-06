#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer common workflow.


is_tty() {
  [[ -t 0 && -t 1 ]]
}

prompt() {
  local label="$1"
  local default_value="${2:-}"
  local secret="${3:-false}"
  local value
  if [[ "${ASSUME_YES:-0}" -eq 1 || ! is_tty ]]; then
    printf '%s\n' "$default_value"
    return
  fi
  if [[ "$secret" == "true" ]]; then
    read -r -s -p "$label${default_value:+ [$default_value]}: " value
    printf '\n' >&2
  else
    read -r -p "$label${default_value:+ [$default_value]}: " value
  fi
  printf '%s\n' "${value:-$default_value}"
}

confirm() {
  local label="$1"
  local default_value="${2:-y}"
  local answer
  if [[ "${ASSUME_YES:-0}" -eq 1 || ! is_tty ]]; then
    [[ "$default_value" =~ ^[Yy]$ ]]
    return
  fi
  read -r -p "$label [${default_value}]: " answer
  answer="${answer:-$default_value}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

choose() {
  local label="$1"
  local default_value="$2"
  shift 2
  local options=("$@")
  local values=()
  local descriptions=()
  local value option option_value option_description max_width=0
  if [[ "${ASSUME_YES:-0}" -eq 1 || ! is_tty ]]; then
    printf '%s\n' "$default_value"
    return
  fi

  for option in "${options[@]}"; do
    option_value="${option%%|*}"
    option_description=""
    if [[ "$option" == *"|"* ]]; then
      option_description="${option#*|}"
    fi
    values+=("$option_value")
    descriptions+=("$option_description")
    if [[ "${#option_value}" -gt "$max_width" ]]; then
      max_width="${#option_value}"
    fi
  done

  printf '\n%s\n' "$label" >&2
  local idx=1
  for option_value in "${values[@]}"; do
    option_description="${descriptions[$((idx - 1))]}"
    printf '  %d. %-*s' "$idx" "$max_width" "$option_value" >&2
    if [[ -n "$option_description" ]]; then
      printf '  %s' "$option_description" >&2
    fi
    printf '\n' >&2
    idx=$((idx + 1))
  done
  printf '\nSelect option [%s]: ' "$default_value" >&2
  if ! read -r value; then
    value=""
  fi
  if [[ -z "$value" ]]; then
    printf '%s\n' "$default_value"
  elif [[ "$value" =~ ^[0-9]+$ && "$value" -ge 1 && "$value" -le "${#values[@]}" ]]; then
    printf '%s\n' "${values[$((value - 1))]}"
  else
    printf '%s\n' "$value"
  fi
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    set +o pipefail
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48
    set -o pipefail
    printf '\n'
  fi
}

require_value() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "$name is required for the selected deployment mode." >&2
    exit 2
  fi
}

require_supported_os() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || true)"
  case "$uname_s" in
    Linux|Darwin)
      ;;
    *)
      echo "Unsupported host OS: ${uname_s:-unknown}" >&2
      echo "Use Linux, macOS, or Windows through WSL2." >&2
      exit 1
      ;;
  esac
  if [[ "$uname_s" == "Linux" && -r /proc/version ]] && grep -qi microsoft /proc/version; then
    echo "Detected WSL. Use a Linux environment with Apptainer available."
  fi
}

ensure_prerequisites() {
  [[ "${INSTALL_PREREQS:-1}" -eq 1 ]] || return 0
  local missing=()
  for cmd in git curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  [[ "${#missing[@]}" -eq 0 ]] && return 0
  echo "Missing prerequisites: ${missing[*]}"
  echo "Install git and curl with your site-approved package manager, then rerun $SCRIPT_NAME."
  exit 1
}

download_with_sha256() {
  local url="$1"
  local sums_url="$2"
  local filename="$3"
  local output="$4"
  local tmp_dir sums_file expected actual

  tmp_dir="$(mktemp -d)"
  sums_file="$tmp_dir/SHASUMS256.txt"
  curl -fsSL "$url" -o "$output"
  if curl -fsSL "$sums_url" -o "$sums_file"; then
    expected="$(awk -v name="$filename" '$2 == name { print $1; exit }' "$sums_file")"
    if [[ -n "$expected" ]] && command -v shasum >/dev/null 2>&1; then
      actual="$(shasum -a 256 "$output" | awk '{print $1}')"
      if [[ "$actual" != "$expected" ]]; then
        echo "Checksum verification failed for $filename" >&2
        rm -rf "$tmp_dir"
        exit 1
      fi
    fi
  fi
  rm -rf "$tmp_dir"
}

normalize_mode() {
  case "$1" in
    local|"1 User, no auth")
      printf 'local\n'
      ;;
    internal|"institutional server")
      printf 'internal\n'
      ;;
    demo|"public demo")
      printf 'demo\n'
      ;;
    *)
      echo "Invalid mode: $1" >&2
      exit 2
      ;;
  esac
}

normalize_provider() {
  case "$1" in
    openai-compatible)
      printf 'openai-compatible\n'
      ;;
    anthropic|Anthropic)
      printf 'anthropic\n'
      ;;
    google|Google)
      printf 'google\n'
      ;;
    ollama|Ollama)
      printf 'ollama\n'
      ;;
    *)
      echo "Invalid LLM provider: $1" >&2
      exit 2
      ;;
  esac
}

find_repo_root() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || pwd)"
  if [[ -f "$dir/scripts/apptainer/up.sh" ]]; then
    printf '%s\n' "$dir"
    return
  fi
  if [[ -f "./scripts/apptainer/up.sh" ]]; then
    pwd
    return
  fi
  printf '\n'
}

ensure_checkout() {
  local root
  root="$(find_repo_root)"
  if [[ -n "$root" ]]; then
    printf '%s\n' "$root"
    return
  fi
  echo "Could not locate a NeuroCade checkout. Use the one-line installer bootstrap or run this script from an existing checkout." >&2
  exit 1
}

setup_install_logging() {
  local root="$1"
  [[ "${INSTALL_LOG_ACTIVE:-0}" -eq 0 ]] || return 0
  mkdir -p "$root/.runtime/logs"
  INSTALL_LOG_FILE="$root/.runtime/logs/install.log"
  INSTALL_LOG_ACTIVE=1
  export INSTALL_LOG_FILE INSTALL_LOG_ACTIVE
  exec > >(tee -a "$INSTALL_LOG_FILE") 2>&1
  echo
  echo "=== NeuroCade installer run: $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
  echo "Log file: $INSTALL_LOG_FILE"
}

log_section() {
  echo
  echo "==> $*"
}

log_step() {
  echo "-- $*"
}

run_step() {
  local label="$1"
  shift
  log_section "$label"
  "$@"
  echo "<== $label complete"
}
