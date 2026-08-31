#!/usr/bin/env bash
# shellcheck shell=bash

runtime_application_exists() {
  [[ -s "$APP_SIF" && -f "$APP_SIF.mode" && "$(sed -n '1p' "$APP_SIF.mode")" == "$APP_SIF_MODE" ]]
}

runtime_pull_application() {
  runtime_application_exists && return
  fail "The application SIF is missing; rerun scripts/install.sh to download the latest release or build from source"
}

runtime_prepare_database() {
  mkdir -p "$APPTAINER_DATABASE_DIR"
  [[ -w "$APPTAINER_DATABASE_DIR" ]] || fail "Apptainer database directory is not writable: $APPTAINER_DATABASE_DIR"
}

build_apptainer_application_command() {
  APPTAINER_APP_COMMAND=(apptainer exec --cleanenv --no-home --containall
    --env-file "$ENV_FILE"
    --bind "$HOST_DATA_DIR:/data" --bind "$APPTAINER_DATABASE_DIR:/database"
    --bind "$BRIDGE_TOKEN_FILE:/run/neurocade/bridge-token:ro")
  [[ -d "$SAMPLE_CASE_DIR" ]] && APPTAINER_APP_COMMAND+=(--bind "$SAMPLE_CASE_DIR:/app/sample_case:ro")
  APPTAINER_APP_COMMAND+=(
    --env NEUROCADE_RUNTIME=apptainer --env NEUROCADE_BRIDGE_URL="http://127.0.0.1:$BRIDGE_PORT"
    --env NEUROCADE_BRIDGE_TOKEN_FILE=/run/neurocade/bridge-token --env HOST_DATA_DIR=/data
    --env NEUROCADE_LAUNCH_ID="$LAUNCH_ID"
    --env DATABASE_URL=sqlite+pysqlite:////database/neurocade.db
    --env NEUROCADE_ACCESS_URL="$(sed -n '1p' "$APP_URL_FILE")" "$APP_SIF"
    python -m uvicorn api_service.main:app --host "$HTTP_BIND" --port "$HTTP_PORT"
  )
}

runtime_start_application() {
  build_apptainer_application_command
  if [[ "$DETACH" -eq 1 ]]; then
    nohup "${APPTAINER_APP_COMMAND[@]}" >>"$APP_LOG" 2>&1 &
    echo "$!" >"$APP_PID_FILE"
  else
    "${APPTAINER_APP_COMMAND[@]}" &
    echo "$!" >"$APP_PID_FILE"
    trap 'stop_application; stop_bridge' EXIT INT TERM
    wait "$(sed -n '1p' "$APP_PID_FILE")"
  fi
}

runtime_stop_application() {
  stop_pid_file "$APP_PID_FILE" "apptainer exec"
}

runtime_reset_database() {
  rm -f \
    "$APPTAINER_DATABASE_DIR/neurocade.db" \
    "$APPTAINER_DATABASE_DIR/neurocade.db-shm" \
    "$APPTAINER_DATABASE_DIR/neurocade.db-wal"
}

runtime_tail_application_logs() {
  tail -n 100 "$APP_LOG" >&2 2>/dev/null || true
}

runtime_follow_logs() {
  touch "$APP_LOG" "$BRIDGE_LOG"
  tail -f "$APP_LOG" "$BRIDGE_LOG"
}
