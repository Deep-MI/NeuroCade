#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer env workflow.


env_quote() {
  local value="${1:-}"
  if [[ "$value" =~ ^[A-Za-z0-9_./:@%+=,-]*$ ]]; then
    printf '%s' "$value"
    return
  fi
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\\$}"
  value="${value//\`/\\\`}"
  printf '"%s"' "$value"
}

env_line() {
  local key="$1"
  local value="${2:-}"
  printf '%s=%s\n' "$key" "$(env_quote "$value")"
}

env_file_value() {
  local root="$1"
  local key="$2"
  local value
  if [[ -f "$root/.env" ]]; then
    value="$(sed -n "s/^${key}=//p" "$root/.env" | tail -n 1)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value//\\\"/\"}"
    value="${value//\\\$/\$}"
    value="${value//\\\\/\\}"
    printf '%s\n' "$value"
  fi
}

env_file_has_key() {
  local root="$1"
  local key="$2"
  [[ -f "$root/.env" ]] && grep -Eq "^${key}=" "$root/.env"
}

env_config_value() {
  local root="$1"
  local key="$2"
  local default_value="${3:-}"
  if env_file_has_key "$root" "$key"; then
    env_file_value "$root" "$key"
  else
    printf '%s\n' "$default_value"
  fi
}

prompt_config_value() {
  local root="$1"
  local key="$2"
  local label="$3"
  local default_value="${4:-}"
  local secret="${5:-false}"
  if env_file_has_key "$root" "$key"; then
    env_file_value "$root" "$key"
  else
    prompt "$label" "$default_value" "$secret"
  fi
}

confirm_config_value() {
  local root="$1"
  local key="$2"
  local label="$3"
  local default_value="${4:-y}"
  local value
  if env_file_has_key "$root" "$key"; then
    value="$(env_file_value "$root" "$key")"
    [[ "$value" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]
  else
    confirm "$label" "$default_value"
  fi
}

env_line_configured() {
  local root="$1"
  local key="$2"
  local default_value="${3:-}"
  env_line "$key" "$(env_config_value "$root" "$key" "$default_value")"
}

neurocade_version_default() {
  local root="$1"
  local tag=""
  if command -v git >/dev/null 2>&1 && [[ -d "$root/.git" ]]; then
    tag="$(git -C "$root" describe --tags --exact-match HEAD 2>/dev/null || true)"
    if [[ -n "$tag" ]]; then
      printf '%s\n' "${tag#v}"
      return
    fi
  fi
  sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$root/client/package.json" | head -n 1
}

freesurfer_home_license_path() {
  local fs_home="${FREESURFER_HOME:-}"
  local candidate
  [[ -n "$fs_home" ]] || return 1
  for candidate in "$fs_home/license.txt" "$fs_home/.license" "$fs_home/.license.txt"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

freesurfer_license_path() {
  local root="$1"
  local explicit_license="${2:-}"
  local fs_license="${FREESURFER_LICENSE:-}"
  local fs_home_license=""
  local env_license=""

  if [[ -f "$root/neurocade-data/license.txt" ]]; then
    printf '%s\n' "$root/neurocade-data/license.txt"
    return 0
  fi
  if [[ -n "$explicit_license" && -f "$explicit_license" ]]; then
    printf '%s\n' "$explicit_license"
    return 0
  fi
  if [[ -n "$fs_license" && -f "$fs_license" ]]; then
    printf '%s\n' "$fs_license"
    return 0
  fi
  if [[ -f "$root/.env" ]]; then
    env_license="$(env_file_value "$root" FREESURFER_LICENSE)"
    if [[ -n "$env_license" && -f "$env_license" ]]; then
      printf '%s\n' "$env_license"
      return 0
    fi
  fi
  fs_home_license="$(freesurfer_home_license_path || true)"
  if [[ -n "$fs_home_license" ]]; then
    printf '%s\n' "$fs_home_license"
    return 0
  fi
  return 1
}

detect_freesurfer_license_default() {
  local root="$1"
  local fs_license="${FREESURFER_LICENSE:-}"
  local fs_home_license=""
  local env_license=""

  if [[ -n "$fs_license" ]]; then
    printf '%s\n' "$fs_license"
    return 0
  fi
  if [[ -f "$root/.env" ]]; then
    env_license="$(env_file_value "$root" FREESURFER_LICENSE)"
    if [[ -n "$env_license" ]]; then
      printf '%s\n' "$env_license"
      return 0
    fi
  fi
  fs_home_license="$(freesurfer_home_license_path || true)"
  if [[ -n "$fs_home_license" ]]; then
    printf '%s\n' "$fs_home_license"
    return 0
  fi
}

install_freesurfer_license_if_available() {
  local root="$1"
  local host_data_dir="$2"
  local explicit_license="${3:-}"
  local license_path=""
  local runtime_license="$host_data_dir/license.txt"

  license_path="$(freesurfer_license_path "$root" "$explicit_license" || true)"
  [[ -n "$license_path" && -f "$license_path" ]] || return 1

  if [[ "$license_path" != "$runtime_license" ]]; then
    mkdir -p "$host_data_dir"
    cp "$license_path" "$runtime_license"
    chmod 600 "$runtime_license" || true
    echo "Detected FreeSurfer license at $license_path"
    echo "Copied FreeSurfer license to $runtime_license"
  else
    echo "Detected FreeSurfer license at $runtime_license"
  fi
  return 0
}

freesurfer_license_available() {
  local root="$1"
  freesurfer_license_path "$root" >/dev/null
}

write_env() {
  local root="$1"
  local mode="$2"
  local provider="$3"
  local env_path="$root/.env"
  local host_data_dir="$root/neurocade-data"
  local app_base_url app_domain acme_email local_auth http_bind http_port
  local clerk_publishable="" clerk_secret="" clerk_jwks="" clerk_issuer="" clerk_audience="" clerk_jwt_template=""
  local llm_backend_url="" llm_backend_key="" llm_backend_model="" llm_provider_default="$provider" workflow_provider_default="$provider"
  local anthropic_key="" anthropic_model="" google_key="" google_model="" ollama_model="" ollama_base_url="http://127.0.0.1:11434"
  local llm_native_tool_calling="false" llm_json_mode="true"

  case "$mode" in
    local)
      local_auth="true"
      app_base_url="http://localhost:8005"
      http_bind="127.0.0.1"
      http_port="8005"
      ;;
    internal)
      local_auth="false"
      app_domain="$(prompt_config_value "$root" APP_DOMAIN "Institutional host name" "$(hostname 2>/dev/null || echo localhost)")"
      app_base_url="$(prompt_config_value "$root" APP_BASE_URL "Application URL for institutional users" "https://$app_domain")"
      http_bind="0.0.0.0"
      http_port="8005"
      ;;
    demo)
      local_auth="false"
      app_domain="$(prompt_config_value "$root" APP_DOMAIN "Public demo domain name" "demo.neurocade.example.org")"
      acme_email="$(prompt_config_value "$root" ACME_EMAIL "External proxy/TLS contact email" "admin@$app_domain")"
      app_base_url="https://$app_domain"
      http_bind="127.0.0.1"
      http_port="8005"
      echo "Demo mode binds locally: configure your external proxy to forward HTTPS to http://127.0.0.1:$http_port."
      ;;
  esac

  local_auth="$(env_config_value "$root" LOCAL_AUTH_ENABLED "$local_auth")"
  app_base_url="$(env_config_value "$root" APP_BASE_URL "$app_base_url")"
  app_domain="$(env_config_value "$root" APP_DOMAIN "${app_domain:-}")"
  acme_email="$(env_config_value "$root" ACME_EMAIL "${acme_email:-}")"
  host_data_dir="$(env_config_value "$root" NEUROCADE_HOST_DATA_DIR "$(env_config_value "$root" HOST_DATA_DIR "$host_data_dir")")"
  http_bind="$(env_config_value "$root" APP_HTTP_BIND "$http_bind")"
  http_port="$(env_config_value "$root" APP_HTTP_PORT "$http_port")"

  if [[ "$mode" != "local" ]]; then
    clerk_publishable="$(prompt_config_value "$root" VITE_CLERK_PUBLISHABLE_KEY "Clerk publishable key" "" true)"
    clerk_secret="$(prompt_config_value "$root" CLERK_SECRET_KEY "Clerk secret key" "" true)"
    clerk_jwks="$(prompt_config_value "$root" CLERK_JWKS_URL "Clerk JWKS URL" "")"
    clerk_issuer="$(prompt_config_value "$root" CLERK_ISSUER "Clerk issuer URL" "")"
    clerk_audience="$(prompt_config_value "$root" CLERK_AUDIENCE "Clerk audience" "neurocade")"
    clerk_jwt_template="$(prompt_config_value "$root" VITE_CLERK_JWT_TEMPLATE "Clerk JWT template name" "$clerk_audience")"
    require_value "Clerk publishable key" "$clerk_publishable"
    require_value "Clerk secret key" "$clerk_secret"
    require_value "Clerk JWKS URL" "$clerk_jwks"
    require_value "Clerk issuer URL" "$clerk_issuer"
    require_value "Clerk audience" "$clerk_audience"
    require_value "Clerk JWT template name" "$clerk_jwt_template"
  fi

  case "$provider" in
    openai-compatible)
      llm_backend_url="$(prompt_config_value "$root" LLM_BACKEND_URL "OpenAI-compatible base URL" "")"
      llm_backend_key="$(prompt_config_value "$root" LLM_BACKEND_API_KEY "OpenAI-compatible API key (optional)" "" true)"
      llm_backend_model="$(prompt_config_value "$root" LLM_BACKEND_MODEL "OpenAI-compatible model" "Qwen/Qwen3.6-35B-A3B")"
      ;;
    anthropic)
      anthropic_key="$(prompt_config_value "$root" ANTHROPIC_API_KEY "Anthropic API key" "" true)"
      anthropic_model="$(prompt_config_value "$root" ANTHROPIC_MODEL "Anthropic model" "claude-3-5-sonnet-latest")"
      llm_backend_url="https://api.openai.com"
      llm_backend_model="unused-openai-compatible-fallback"
      ;;
    google)
      google_key="$(prompt_config_value "$root" GOOGLE_API_KEY "Google Generative AI API key" "" true)"
      google_model="$(prompt_config_value "$root" GOOGLE_MODEL "Google model" "gemini-2.0-flash")"
      llm_backend_url="https://api.openai.com"
      llm_backend_model="unused-openai-compatible-fallback"
      ;;
    ollama)
      ollama_model="$(prompt_config_value "$root" OLLAMA_MODEL "Ollama model" "gemma4:e2b")"
      ollama_base_url="$(env_config_value "$root" OLLAMA_BASE_URL "$ollama_base_url")"
      llm_backend_url="$ollama_base_url"
      llm_backend_model="$ollama_model"
      llm_backend_key=""
      llm_provider_default="ollama"
      workflow_provider_default="ollama"
      ;;
    no-llm)
      llm_backend_url=""
      llm_backend_key=""
      llm_backend_model="no-llm"
      llm_provider_default="no-llm"
      workflow_provider_default="no-llm"
      ;;
  esac

  if [[ "$provider" != "no-llm" ]]; then
    llm_backend_url="$(env_config_value "$root" LLM_BACKEND_URL "$llm_backend_url")"
    llm_backend_key="$(env_config_value "$root" LLM_BACKEND_API_KEY "$llm_backend_key")"
    llm_backend_model="$(env_config_value "$root" LLM_BACKEND_MODEL "$llm_backend_model")"
  fi

  echo
  echo "FreeSurfer license note:"
  echo "  A free license document is required for FastSurfer and FreeSurfer toolboxes."
  echo "  Register here: $FREESURFER_LICENSE_URL and provide the license path, or provide the path to your existing license"
  local freesurfer_license freesurfer_license_default installed_freesurfer_license=""
  freesurfer_license_default="$(detect_freesurfer_license_default "$root")"
  if [[ -n "$freesurfer_license_default" ]]; then
    echo "Detected FREESURFER_LICENSE=$freesurfer_license_default"
  fi
  local freesurfer_license_label="FreeSurfer license file path"
  if [[ -z "$freesurfer_license_default" ]]; then
    freesurfer_license_label="FreeSurfer license file path (press Enter to add the license later)"
  fi
  freesurfer_license="$(prompt_config_value "$root" FREESURFER_LICENSE "$freesurfer_license_label" "$freesurfer_license_default")"
  local llm_api_token
  llm_api_token="$(env_config_value "$root" LLM_API_TOKEN "$(random_secret)")"

  if [[ -f "$env_path" ]]; then
    local backup="$env_path.backup.$(date +%Y%m%d%H%M%S)"
    cp "$env_path" "$backup"
    echo "Backed up existing .env to $backup"
  fi

  mkdir -p "$host_data_dir/output"
  if install_freesurfer_license_if_available "$root" "$host_data_dir" "$freesurfer_license"; then
    installed_freesurfer_license="$host_data_dir/license.txt"
  fi
  local tmp_env_path
  tmp_env_path="$(mktemp "$root/.env.tmp.XXXXXX")"
  {
    echo "# Generated by scripts/install.sh"
    env_line DEPLOYMENT_PROFILE "$mode"
    env_line_configured "$root" APP_NAME "$APP_DISPLAY_NAME"
    env_line_configured "$root" NEUROCADE_VERSION "$(neurocade_version_default "$root")"
    env_line_configured "$root" NEUROCADE_VERSION_CHECK_URL "${NEUROCADE_VERSION_CHECK_URL:-https://NeuroCade.org/latest.json}"
    env_line_configured "$root" NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS "${NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS:-86400}"
    env_line APP_BASE_URL "$app_base_url"
    env_line_configured "$root" APP_PUBLIC_URL "$app_base_url"
    env_line_configured "$root" APP_ALLOWED_HOSTS "${app_domain:+$app_domain,}localhost,127.0.0.1"
    env_line APP_DOMAIN "${app_domain:-}"
    env_line ACME_EMAIL "${acme_email:-}"
    echo
    env_line_configured "$root" UID "$(id -u)"
    env_line_configured "$root" GID "$(id -g)"
    env_line HOST_DATA_DIR "$host_data_dir"
    env_line_configured "$root" NEUROCADE_HOST_DATA_DIR "$host_data_dir"
    env_line TOOL_CATALOG_DIR "$root/llm-data/tool-catalog"
    env_line NEUROCADE_BASH_IMAGE "neurocade-runtime-bash:local"
    env_line_configured "$root" NEUROCADE_SIF_DIR "$host_data_dir/sif"
    env_line_configured "$root" NEUROCADE_DOCKER_GPU "false"
    env_line_configured "$root" NEUROCADE_CONTAINER_RELEASE_TAG "${NEUROCADE_CONTAINER_RELEASE_TAG:-latest}"
    env_line NEUROCADE_CONTAINER_INVENTORY "$root/llm-data/tool-catalog/installed_containers.json"
    env_line NEUROCADE_INSTALLED_TOOLS_JSONL "$root/llm-data/tool-catalog/installed_tools.jsonl"
    env_line_configured "$root" FASTSURFER_DEVICE_MODE "auto"
    env_line_configured "$root" WORKER_CONCURRENCY "2"
    env_line_configured "$root" API_WORKER_CONCURRENCY "2"
    env_line_configured "$root" MAX_UPLOAD_FILE_SIZE_BYTES "2147483648"
    env_line_configured "$root" DICOM_ZIP_MAX_ENTRIES "5000"
    env_line_configured "$root" DICOM_ZIP_MAX_EXPANDED_BYTES "4294967296"
    env_line_configured "$root" DICOM_RAW_RETENTION "discard"
    env_line_configured "$root" HUGGING_FACE_HUB_TOKEN "NOTSET"
    echo
    env_line APP_HTTP_BIND "$http_bind"
    env_line APP_HTTP_PORT "$http_port"
    echo
    # SQLite is the only database; default to a file under the data dir.
    env_line_configured "$root" DATABASE_URL "sqlite+pysqlite:///$host_data_dir/neurocade.db"
    # Tool runtime: apptainer (default) or docker (native dev).
    env_line_configured "$root" NEUROCADE_RUNTIME_BACKEND "apptainer"
    echo
    env_line LOCAL_AUTH_ENABLED "$local_auth"
    env_line_configured "$root" LOCAL_AUTH_USER_ID "local-user"
    env_line_configured "$root" LOCAL_AUTH_EMAIL "local@example.com"
    env_line_configured "$root" LOCAL_AUTH_NAME "Local User"
    env_line VITE_CLERK_PUBLISHABLE_KEY "$clerk_publishable"
    env_line VITE_CLERK_JWT_TEMPLATE "$clerk_jwt_template"
    env_line CLERK_SECRET_KEY "$clerk_secret"
    env_line CLERK_JWKS_URL "$clerk_jwks"
    env_line CLERK_ISSUER "$clerk_issuer"
    env_line CLERK_AUDIENCE "$clerk_audience"
    echo
    env_line LLM_API_TOKEN "$llm_api_token"
    env_line LLM_PROVIDER_DEFAULT "$llm_provider_default"
    env_line WORKFLOW_DEFAULT_PROVIDER "$workflow_provider_default"
    env_line LLM_BACKEND_URL "$llm_backend_url"
    env_line LLM_BACKEND_API_KEY "$llm_backend_key"
    env_line LLM_BACKEND_MODEL "$llm_backend_model"
    env_line_configured "$root" LLM_NATIVE_TOOL_CALLING "$llm_native_tool_calling"
    env_line_configured "$root" LLM_JSON_MODE "$llm_json_mode"
    env_line_configured "$root" ANTHROPIC_API_KEY "$anthropic_key"
    env_line_configured "$root" ANTHROPIC_MODEL "$anthropic_model"
    env_line_configured "$root" GOOGLE_API_KEY "$google_key"
    env_line_configured "$root" GOOGLE_MODEL "$google_model"
    env_line_configured "$root" OLLAMA_BASE_URL "$ollama_base_url"
    env_line_configured "$root" OLLAMA_MODEL "$ollama_model"
    echo
    env_line_configured "$root" FREESURFER_LICENSE "$installed_freesurfer_license"
  } >"$tmp_env_path"
  chmod 600 "$tmp_env_path"
  mv "$tmp_env_path" "$env_path"

  if [[ -z "$installed_freesurfer_license" && ! -f "$host_data_dir/license.txt" ]]; then
    echo
    echo "Note: no FreeSurfer license was provided."
    echo "A FreeSurfer license is highly recommended and is required for the full FastSurfer MRI pipeline."
    echo "The desktop/web app can still start, but real MRI processing will need that license later."
    echo "You can satisfy that by either:"
    echo "  - registering for a free license at $FREESURFER_LICENSE_URL"
    echo "  - rerunning install with FREESURFER_LICENSE=/path/to/license.txt, or"
    echo "  - placing a license at $host_data_dir/license.txt"
  fi
}
