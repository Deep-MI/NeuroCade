#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer up workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

detach=0
for arg in "$@"; do
  case "$arg" in
    -d|--detach)
      detach=1
      ;;
    *)
      echo "Unsupported argument for scripts/apptainer/up.sh: $arg" >&2
      exit 2
      ;;
  esac
done

APPTAINER_UP_LOG_OFFSETS="$RUNTIME_DIR/logs/apptainer-up.offsets"

timing_now_ms() {
  if command -v perl >/dev/null 2>&1; then
    perl -MTime::HiRes=time -e 'printf "%.0f\n", time() * 1000'
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import time; print(int(time.time() * 1000))'
    return 0
  fi
  printf '%s000\n' "$(date +%s)"
}

timing_format_ms() {
  local ms="$1"
  printf '%d.%03ds' "$((ms / 1000))" "$((ms % 1000))"
}

timing_total_start_ms="$(timing_now_ms)"
timing_phase_start_ms="$timing_total_start_ms"
timing_phase_name="startup"

timing_start_phase() {
  timing_phase_name="$1"
  timing_phase_start_ms="$(timing_now_ms)"
  echo "[startup] $timing_phase_name..."
}

timing_end_phase() {
  local now elapsed total
  now="$(timing_now_ms)"
  elapsed=$((now - timing_phase_start_ms))
  total=$((now - timing_total_start_ms))
  echo "[startup timing] $timing_phase_name: $(timing_format_ms "$elapsed") (total $(timing_format_ms "$total"))"
}

record_runtime_log_offsets() {
  local service log_file offset
  : >"$APPTAINER_UP_LOG_OFFSETS"
  for service in "${RUNTIME_LOG_SERVICES[@]}"; do
    log_file="$(service_log_file "$service")"
    if is_service_running "$service"; then
      offset="$(file_size_bytes "$log_file")"
    else
      offset=0
    fi
    printf '%s %s\n' "$service" "$offset" >>"$APPTAINER_UP_LOG_OFFSETS"
  done
}

record_runtime_log_offsets

timing_start_phase "Apptainer availability and Lima mount checks"
require_apptainer
ensure_lima_checkout_mount_live_writable
timing_end_phase

timing_start_phase "Infrastructure image checks"
"$SCRIPT_DIR/images.sh" infra
timing_end_phase

timing_start_phase "Python interpreter resolution"
PYTHON_BIN="$(python_bin)"
timing_end_phase

echo "Checking core runtime containers..."
timing_start_phase "Core runtime container checks"
if "$ROOT_DIR/scripts/containers.sh" check core --fast; then
  :
else
  "$ROOT_DIR/scripts/containers.sh" install core --no-harvest-help
fi
timing_end_phase

timing_start_phase "Python runtime dependency check"
if PYTHONPATH="$ROOT_DIR/packages/neurocade-runtime-tools/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "$ROOT_DIR/scripts/check_python_runtime.py" --pyproject "$ROOT_DIR/pyproject.toml" --sentinel "$RUNTIME_DIR/python-deps.ok"; then
  :
else
  if command -v uv >/dev/null 2>&1; then
    UV_CACHE_DIR="${UV_CACHE_DIR:-$RUNTIME_DIR/uv-cache}" uv pip install --python "$PYTHON_BIN" -q -r "$ROOT_DIR/pyproject.toml"
    PYTHONPATH="$ROOT_DIR/packages/neurocade-runtime-tools/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "$ROOT_DIR/scripts/check_python_runtime.py" --pyproject "$ROOT_DIR/pyproject.toml" --sentinel "$RUNTIME_DIR/python-deps.ok"
  else
    echo "uv is required to install Python runtime dependencies." >&2
    echo "Install uv, then rerun: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
  fi
fi
timing_end_phase

timing_start_phase "Client dependency/build check"
case "$CLIENT_SERVE_MODE" in
  static|vite)
    ;;
  *)
    echo "Invalid CLIENT_SERVE_MODE: $CLIENT_SERVE_MODE. Use static or vite." >&2
    exit 1
    ;;
esac

if [[ "$CLIENT_SERVE_MODE" == "vite" && ! -d "$ROOT_DIR/client/node_modules/react" ]]; then
  (cd "$ROOT_DIR/client" && npm ci)
fi
if [[ "$CLIENT_SERVE_MODE" == "static" ]]; then
  client_dist_index="$ROOT_DIR/client/dist/index.html"
  client_build_env="$ROOT_DIR/client/dist/.neurocade-build-env"
  client_current_env="$(printf 'VITE_API_URL=%s\nVITE_CLERK_PUBLISHABLE_KEY=%s\nVITE_CLERK_JWT_TEMPLATE=%s\nVITE_LOCAL_AUTH_ENABLED=%s\n' "/api/app" "${VITE_CLERK_PUBLISHABLE_KEY:-}" "${VITE_CLERK_JWT_TEMPLATE:-}" "${LOCAL_AUTH_ENABLED:-false}")"
  client_build_needed=0
  if [[ ! -f "$client_dist_index" ]]; then
    client_build_needed=1
  elif [[ ! -f "$client_build_env" ]] || [[ "$(<"$client_build_env")" != "$client_current_env" ]]; then
    client_build_needed=1
  elif find \
    "$ROOT_DIR/client/src" \
    "$ROOT_DIR/client/public" \
    "$ROOT_DIR/client/index.html" \
    "$ROOT_DIR/client/package.json" \
    "$ROOT_DIR/client/package-lock.json" \
    "$ROOT_DIR/client/vite.config.ts" \
    "$ROOT_DIR/client/tsconfig.json" \
    "$ROOT_DIR/client/tsconfig.app.json" \
    "$ROOT_DIR/client/tsconfig.node.json" \
    -newer "$client_dist_index" -print -quit | grep -q .; then
    client_build_needed=1
  fi
  if (( client_build_needed )); then
    echo "Building production client bundle..."
    if [[ ! -d "$ROOT_DIR/client/node_modules/react" ]]; then
      (cd "$ROOT_DIR/client" && npm ci)
    fi
    (cd "$ROOT_DIR/client" && VITE_API_URL=/api/app VITE_CLERK_PUBLISHABLE_KEY="${VITE_CLERK_PUBLISHABLE_KEY:-}" VITE_CLERK_JWT_TEMPLATE="${VITE_CLERK_JWT_TEMPLATE:-}" VITE_LOCAL_AUTH_ENABLED="${LOCAL_AUTH_ENABLED:-false}" npm run build)
    printf '%s' "$client_current_env" >"$client_build_env"
  else
    echo "Production client bundle already up to date."
  fi
fi
timing_end_phase

timing_start_phase "Postgres data directory check"
if [[ ! -s "$RUNTIME_DIR/postgres/PG_VERSION" ]]; then
  pwfile="$RUNTIME_DIR/postgres-password"
  printf '%s\n' "$POSTGRES_PASSWORD" >"$pwfile"
  "$APPTAINER_BIN" exec --cleanenv --bind "$RUNTIME_DIR:/runtime:rw" --bind "$RUNTIME_DIR/postgres:/var/lib/postgresql/data:rw" "$POSTGRES_SIF" \
    initdb -D /var/lib/postgresql/data -U "$POSTGRES_USER" --pwfile=/runtime/postgres-password
fi
if [[ -f "$RUNTIME_DIR/postgres/postmaster.pid" ]] && ! "$APPTAINER_BIN" exec --cleanenv "$POSTGRES_SIF" pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; then
  echo "Removing stale Postgres pid file from a previous interrupted VM run."
  rm -f "$RUNTIME_DIR/postgres/postmaster.pid"
fi
timing_end_phase

timing_start_phase "Start Postgres and Redis"
start_service postgres "$APPTAINER_BIN" exec --cleanenv --bind "$RUNTIME_DIR/postgres:/var/lib/postgresql/data:rw" "$POSTGRES_SIF" \
  postgres -D /var/lib/postgresql/data -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -c unix_socket_directories=/tmp

start_service redis "$APPTAINER_BIN" exec --cleanenv --bind "$RUNTIME_DIR/redis:/data:rw" --bind "$ROOT_DIR/config/redis.conf:/usr/local/etc/redis/redis.conf:ro" "$REDIS_SIF" \
  redis-server /usr/local/etc/redis/redis.conf --requirepass "$REDIS_PASSWORD" --bind "$REDIS_HOST" --port "$REDIS_PORT" --dir /data
timing_end_phase

timing_start_phase "Postgres readiness"
postgres_deadline=$((SECONDS + ${POSTGRES_READY_TIMEOUT:-300}))
until "$APPTAINER_BIN" exec --cleanenv "$POSTGRES_SIF" pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; do
  if (( SECONDS >= postgres_deadline )); then
    break
  fi
  sleep 1
done
if (( SECONDS >= postgres_deadline )) && ! "$APPTAINER_BIN" exec --cleanenv "$POSTGRES_SIF" pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; then
  echo "Postgres did not become ready on $POSTGRES_HOST:$POSTGRES_PORT." >&2
  exit 1
fi
timing_end_phase

timing_start_phase "Database existence check"
db_name_sql="$(printf '%s' "$POSTGRES_DB" | sed "s/'/''/g")"
db_exists="$("$APPTAINER_BIN" exec --cleanenv "$POSTGRES_SIF" env PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -tA -c "SELECT 1 FROM pg_database WHERE datname = '$db_name_sql'" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "$db_exists" != "1" ]]; then
  "$APPTAINER_BIN" exec --cleanenv "$POSTGRES_SIF" env PGPASSWORD="$POSTGRES_PASSWORD" createdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$POSTGRES_DB"
fi
timing_end_phase

timing_start_phase "Redis readiness"
redis_deadline=$((SECONDS + ${REDIS_READY_TIMEOUT:-60}))
until "$APPTAINER_BIN" exec --cleanenv "$REDIS_SIF" redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" ping >/dev/null 2>&1; do
  if (( SECONDS >= redis_deadline )); then
    break
  fi
  sleep 1
done
if (( SECONDS >= redis_deadline )) && ! "$APPTAINER_BIN" exec --cleanenv "$REDIS_SIF" redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" ping >/dev/null 2>&1; then
  echo "Redis did not become ready on $REDIS_HOST:$REDIS_PORT." >&2
  exit 1
fi
timing_end_phase

timing_start_phase "Traefik dynamic config generation"
TRAEFIK_TARGET_HOST="127.0.0.1"
if [[ "$APPTAINER_BIN" == "$ROOT_DIR/.apptainer/bin/apptainer" ]]; then
  TRAEFIK_TARGET_HOST="host.lima.internal"
fi

cat >"$RUNTIME_DIR/traefik/traefik-dynamic.yml" <<EOF
http:
  routers:
    app-api:
      rule: "PathPrefix(\`/api/app\`)"
      service: app-api
      priority: 100
    client:
      rule: "PathPrefix(\`/\`)"
      service: client
      priority: 1
  services:
    app-api:
      loadBalancer:
        servers:
          - url: "http://$TRAEFIK_TARGET_HOST:$API_SERVICE_PORT"
    client:
      loadBalancer:
        servers:
          - url: "http://$TRAEFIK_TARGET_HOST:$CLIENT_PORT"
EOF
timing_end_phase

COMMON_ENV=(
  env
  "DEPLOYMENT_PROFILE=${DEPLOYMENT_PROFILE:-local}"
  "APP_BASE_URL=${APP_BASE_URL:-http://localhost:8005}"
  "APP_PUBLIC_URL=${APP_PUBLIC_URL:-${APP_BASE_URL:-http://localhost:8005}}"
  "APP_ALLOWED_HOSTS=${APP_ALLOWED_HOSTS:-}"
  "HOST_DATA_DIR=$HOST_DATA_DIR"
  "REDIS_URL=$REDIS_URL"
  "DATABASE_URL=$DATABASE_URL"
  "POSTGRES_HOST=$POSTGRES_HOST"
  "POSTGRES_PORT=$POSTGRES_PORT"
  "API_SERVICE_URL=http://$API_SERVICE_HOST:$API_SERVICE_PORT"
  "HOST_RUNTIME_RUNNER_URL=$HOST_RUNTIME_RUNNER_URL"
  "HOST_RUNTIME_RUNNER_TOKEN=${HOST_RUNTIME_RUNNER_TOKEN:-}"
  "NEUROCADE_CONFIG_DIR=$ROOT_DIR/config"
  "VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY:-}"
  "VITE_CLERK_JWT_TEMPLATE=${VITE_CLERK_JWT_TEMPLATE:-}"
  "CLERK_SECRET_KEY=${CLERK_SECRET_KEY:-}"
  "CLERK_JWKS_URL=${CLERK_JWKS_URL:-}"
  "CLERK_ISSUER=${CLERK_ISSUER:-}"
  "CLERK_AUDIENCE=${CLERK_AUDIENCE:-}"
  "LOCAL_AUTH_ENABLED=${LOCAL_AUTH_ENABLED:-false}"
  "FREESURFER_LICENSE=${FREESURFER_LICENSE:-}"
  "NEUROCADE_CONTAINER_ROOT=$NEUROCADE_CONTAINER_ROOT"
  "NEUROCADE_CONTAINER_INVENTORY=$NEUROCADE_CONTAINER_INVENTORY"
  "NEUROCADE_INSTALLED_TOOLS_JSONL=$NEUROCADE_INSTALLED_TOOLS_JSONL"
  "MAX_UPLOAD_FILE_SIZE_BYTES=${MAX_UPLOAD_FILE_SIZE_BYTES:-2147483648}"
  "DICOM_ZIP_MAX_ENTRIES=${DICOM_ZIP_MAX_ENTRIES:-5000}"
  "DICOM_ZIP_MAX_EXPANDED_BYTES=${DICOM_ZIP_MAX_EXPANDED_BYTES:-4294967296}"
  "DICOM_RAW_RETENTION=${DICOM_RAW_RETENTION:-discard}"
  "PYTHONDONTWRITEBYTECODE=1"
)

timing_start_phase "Start host runtime runner"
start_service host-runtime-runner "${COMMON_ENV[@]}" PYTHONPATH="$ROOT_DIR/api-service:$ROOT_DIR" "$PYTHON_BIN" -m uvicorn api_service.host_runtime_runner:app --host "$HOST_RUNTIME_RUNNER_HOST" --port "$HOST_RUNTIME_RUNNER_PORT"
wait_for_url "http://$HOST_RUNTIME_RUNNER_HOST:$HOST_RUNTIME_RUNNER_PORT/healthz" 60
timing_end_phase

timing_start_phase "Start API service, worker, client, and update checker"
start_service api-service "${COMMON_ENV[@]}" PYTHONPATH="$ROOT_DIR/api-service:$ROOT_DIR" "$PYTHON_BIN" -m uvicorn api_service.main:app --host "$API_SERVICE_HOST" --port "$API_SERVICE_PORT"
start_service api-worker "${COMMON_ENV[@]}" PYTHONPATH="$ROOT_DIR/api-service:$ROOT_DIR" "$PYTHON_BIN" -m celery --workdir "$ROOT_DIR/api-service" -A api_service.celery_app worker --loglevel=info --concurrency="${API_WORKER_CONCURRENCY:-2}" -Q workspace_batch,fastsurfer -n api-worker@%h
if [[ "$CLIENT_SERVE_MODE" == "vite" ]]; then
  start_service client env HOME="$RUNTIME_DIR/home" NPM_CONFIG_CACHE="$RUNTIME_DIR/npm-cache" VITE_API_URL=/api/app VITE_CLERK_PUBLISHABLE_KEY="${VITE_CLERK_PUBLISHABLE_KEY:-}" VITE_CLERK_JWT_TEMPLATE="${VITE_CLERK_JWT_TEMPLATE:-}" VITE_LOCAL_AUTH_ENABLED="${LOCAL_AUTH_ENABLED:-false}" npm --prefix "$ROOT_DIR/client" run dev -- --host "$CLIENT_HOST" --port "$CLIENT_PORT" --strictPort
else
  start_service client "$PYTHON_BIN" "$ROOT_DIR/scripts/serve_static_client.py" --directory "$ROOT_DIR/client/dist" --host "$CLIENT_HOST" --port "$CLIENT_PORT"
fi
start_service update-checker "$PYTHON_BIN" "$ROOT_DIR/scripts/update_checker.py"
timing_end_phase

timing_start_phase "Start Traefik"
traefik_args=(
  traefik
  --providers.file.filename=/config/traefik-dynamic.yml
)
TRAEFIK_ENTRYPOINT_BIND="$APP_HTTP_BIND"
if [[ "$APPTAINER_BIN" == "$ROOT_DIR/.apptainer/bin/apptainer" ]]; then
  if [[ "$TRAEFIK_ENTRYPOINT_BIND" == "127.0.0.1" || "$TRAEFIK_ENTRYPOINT_BIND" == "localhost" ]]; then
    TRAEFIK_ENTRYPOINT_BIND="0.0.0.0"
  fi
  echo "Skipping Traefik dashboard for Lima-backed Apptainer; the app gateway remains enabled."
elif [[ "${TRAEFIK_DASHBOARD_ENABLED:-false}" == "true" || "${TRAEFIK_API_INSECURE:-false}" == "true" ]]; then
  if [[ "${TRAEFIK_API_INSECURE:-false}" == "true" && "$TRAEFIK_DASHBOARD_PORT" == "8080" ]]; then
    cat >&2 <<EOF
Invalid Traefik dashboard configuration.

TRAEFIK_API_INSECURE=true creates Traefik's built-in insecure API listener on :8080.
The NeuroCade dashboard entrypoint is also configured for TRAEFIK_DASHBOARD_PORT=8080,
so Traefik would fail to start and the desktop launcher would wait on the gateway.

Set TRAEFIK_API_INSECURE=false, or choose a different TRAEFIK_DASHBOARD_PORT.
EOF
    exit 1
  fi
  if (exec 3<>"/dev/tcp/$TRAEFIK_DASHBOARD_BIND/$TRAEFIK_DASHBOARD_PORT") >/dev/null 2>&1; then
    echo "Skipping Traefik dashboard: $TRAEFIK_DASHBOARD_BIND:$TRAEFIK_DASHBOARD_PORT is already in use." >&2
  else
    traefik_args+=(
      --api.dashboard="${TRAEFIK_DASHBOARD_ENABLED:-true}"
      --api.insecure="${TRAEFIK_API_INSECURE:-true}"
      --entrypoints.dashboard.address="$TRAEFIK_DASHBOARD_BIND:$TRAEFIK_DASHBOARD_PORT"
    )
  fi
fi
traefik_args+=(--entrypoints.web.address="$TRAEFIK_ENTRYPOINT_BIND:$APP_HTTP_PORT")
start_service traefik "$APPTAINER_BIN" exec --cleanenv --bind "$RUNTIME_DIR/traefik:/config:ro" "$TRAEFIK_SIF" "${traefik_args[@]}"
timing_end_phase

timing_start_phase "API health readiness"
wait_for_url "http://$API_SERVICE_HOST:$API_SERVICE_PORT/api/app/healthz" 180
timing_end_phase
display_host="$APP_HTTP_BIND"
if [[ "$display_host" == "0.0.0.0" || "$display_host" == "::" ]]; then
  display_host="${APP_HOST:-127.0.0.1}"
fi
echo "${APP_NAME:-NeuroCade} Apptainer stack is running at http://$display_host:$APP_HTTP_PORT"
if [[ "$display_host" != "$APP_HTTP_BIND" ]]; then
  echo "Listening on $APP_HTTP_BIND:$APP_HTTP_PORT"
fi
timing_total_end_ms="$(timing_now_ms)"
echo "[startup timing] total: $(timing_format_ms "$((timing_total_end_ms - timing_total_start_ms))")"

if [[ "$detach" -eq 0 ]]; then
  echo "Tailing logs. Press Ctrl-C to stop tailing; services keep running."
  "$SCRIPT_DIR/logs.sh" --from-offsets "$APPTAINER_UP_LOG_OFFSETS" -f
fi
