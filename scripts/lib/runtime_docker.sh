#!/usr/bin/env bash
# shellcheck shell=bash

runtime_application_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

runtime_pull_application() {
  if [[ -n "$DOCKER_PLATFORM" ]]; then
    docker pull --platform "$DOCKER_PLATFORM" "$IMAGE"
  else
    docker pull "$IMAGE"
  fi
}

runtime_prepare_database() {
  if ! docker volume inspect "$DATABASE_VOLUME" >/dev/null 2>&1; then
    [[ -n "$INSTALL_ID" ]] || fail "The NeuroCade installation ID is missing"
    docker volume create \
      --label org.neurocade.managed=true \
      --label "org.neurocade.install-id=$INSTALL_ID" \
      "$DATABASE_VOLUME" >/dev/null
  fi
  local -a volume_init_args=(docker run --rm --user 0)
  [[ -n "$DOCKER_PLATFORM" ]] && volume_init_args+=(--platform "$DOCKER_PLATFORM")
  volume_init_args+=(
    -v "$DATABASE_VOLUME:/database"
    --entrypoint sh "$IMAGE"
    -c 'chown "$1:$2" /database' _ "$(id -u)" "$(id -g)"
  )
  "${volume_init_args[@]}"
}

docker_run_args() {
  write_docker_env_file "$ENV_FILE" "$DOCKER_ENV_FILE"
  DOCKER_APP_ARGS=(docker run --name "$CONTAINER_NAME" --label "org.neurocade.launch-id=$LAUNCH_ID" --label "org.neurocade.install-id=${INSTALL_ID:-}" --user "$(id -u):$(id -g)" --add-host host.docker.internal:host-gateway)
  [[ -n "$DOCKER_PLATFORM" ]] && DOCKER_APP_ARGS+=(--platform "$DOCKER_PLATFORM")
  DOCKER_APP_ARGS+=(
    -v "$HOST_DATA_DIR:/data" -v "$DATABASE_VOLUME:/database"
    -v "$BRIDGE_TOKEN_FILE:/run/neurocade/bridge-token:ro"
    -p "$HTTP_BIND:$HTTP_PORT:8000" --env-file "$DOCKER_ENV_FILE"
    -e NEUROCADE_RUNTIME=docker -e NEUROCADE_BRIDGE_URL="http://host.docker.internal:$BRIDGE_PORT"
    -e NEUROCADE_BRIDGE_TOKEN_FILE=/run/neurocade/bridge-token -e HOST_DATA_DIR=/data
    -e NEUROCADE_LAUNCH_ID="$LAUNCH_ID"
    -e NEUROCADE_MCP_HOST_EXECUTABLE="$BRIDGE_VENV/bin/neurocade-mcp"
    -e NEUROCADE_MCP_ENABLED="$MCP_ENABLED" -e NEUROCADE_MCP_ACCESS="$MCP_ACCESS" -e APP_HTTP_BIND="$HTTP_BIND"
    -e DATABASE_URL=sqlite+pysqlite:////database/neurocade.db -e HOME=/tmp
    -e NEUROCADE_ACCESS_URL="$(sed -n '1p' "$APP_URL_FILE")"
  )
  [[ -d "$SAMPLE_CASE_DIR" ]] && DOCKER_APP_ARGS+=(-v "$SAMPLE_CASE_DIR:/app/sample_case:ro")
}

runtime_start_application() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker_run_args
  if [[ "$DETACH" -eq 1 ]]; then
    "${DOCKER_APP_ARGS[@]}" -d --restart unless-stopped "$IMAGE"
  else
    trap 'stop_mcp_discovery; stop_application; stop_bridge' EXIT INT TERM
    "${DOCKER_APP_ARGS[@]}" --rm "$IMAGE" &
    wait "$!"
  fi
}

runtime_stop_application() {
  docker stop --time 15 "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

runtime_reset_database() {
  docker volume rm "$DATABASE_VOLUME" >/dev/null 2>&1 || true
}

runtime_tail_application_logs() {
  docker logs --tail 100 "$CONTAINER_NAME" >&2 2>/dev/null || true
}

runtime_follow_logs() {
  touch "$BRIDGE_LOG"
  tail -n 100 -f "$BRIDGE_LOG" &
  BRIDGE_TAIL_PID=$!
  trap 'kill "$BRIDGE_TAIL_PID" >/dev/null 2>&1 || true' EXIT INT TERM
  docker logs -f "$CONTAINER_NAME"
}
