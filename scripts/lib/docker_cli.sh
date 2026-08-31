#!/usr/bin/env bash

configure_docker_cli_path() {
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  command -v docker-credential-desktop >/dev/null 2>&1 && return 0

  local docker_cli_dir
  for docker_cli_dir in \
    "/Applications/Docker.app/Contents/Resources/bin" \
    "${HOME}/Applications/Docker.app/Contents/Resources/bin"
  do
    if [[ -x "$docker_cli_dir/docker-credential-desktop" ]]; then
      export PATH="$PATH:$docker_cli_dir"
      return 0
    fi
  done
}
