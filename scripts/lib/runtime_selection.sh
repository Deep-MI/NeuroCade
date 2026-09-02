#!/usr/bin/env bash

USER_NAMESPACE_FILE="${USER_NAMESPACE_FILE:-/proc/sys/user/max_user_namespaces}"

docker_runtime_available() {
  command -v docker >/dev/null 2>&1
}

rootless_apptainer_available() {
  local max_user_namespaces
  [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || return 1
  [[ "$(id -u)" -ne 0 ]] || return 1
  command -v apptainer >/dev/null 2>&1 || return 1
  [[ -r "$USER_NAMESPACE_FILE" ]] || return 1
  max_user_namespaces="$(sed -n '1p' "$USER_NAMESPACE_FILE" 2>/dev/null)"
  [[ "$max_user_namespaces" =~ ^[0-9]+$ ]] && (( max_user_namespaces > 0 )) || return 1
  apptainer exec --help 2>&1 | grep -q -- '--no-home'
}

default_runtime() {
  case "$(uname -s)" in
    Darwin)
      docker_runtime_available || {
        echo "Docker is required on macOS." >&2
        return 1
      }
      printf 'docker\n'
      ;;
    Linux)
      if rootless_apptainer_available; then
        printf 'apptainer\n'
      elif docker_runtime_available; then
        printf 'docker\n'
      else
        echo "Install rootless Apptainer or Docker before installing NeuroCade." >&2
        return 1
      fi
      ;;
    *)
      echo "NeuroCade supports macOS with Docker or Linux with rootless Apptainer/Docker." >&2
      return 1
      ;;
  esac
}

validate_runtime() {
  case "$1" in
    docker)
      docker_runtime_available || {
        echo "Docker is required for --runtime docker." >&2
        return 1
      }
      ;;
    apptainer)
      [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || {
        echo "Apptainer installs require Linux amd64." >&2
        return 1
      }
      [[ "$(id -u)" -ne 0 ]] || {
        echo "Apptainer installs must run as a non-root user." >&2
        return 1
      }
      command -v apptainer >/dev/null 2>&1 || {
        echo "Apptainer is required for --runtime apptainer." >&2
        return 1
      }
      rootless_apptainer_available || {
        echo "Apptainer must support rootless execution with user namespaces and --no-home." >&2
        return 1
      }
      ;;
    *)
      echo "Runtime must be docker or apptainer." >&2
      return 1
      ;;
  esac
}
