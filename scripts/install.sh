#!/usr/bin/env bash
# NeuroCade matched-runtime installer.
set -euo pipefail

ARCHIVE_URL="${NEUROCADE_ARCHIVE_URL:-https://github.com/Deep-MI/NeuroCade/archive/refs/heads/main.tar.gz}"
DEFAULT_INSTALL_DIR="${NEUROCADE_INSTALL_DIR:-$HOME/NeuroCade}"
DEFAULT_IMAGE="docker.io/deepmi/neurocade:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"

usage() {
  cat <<'EOF'
NeuroCade runtime installer

Quick install:
  bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh)

From a checkout:
  ./scripts/install.sh

Options:
  --runtime docker|apptainer      Override automatic runtime selection.
                                  Defaults to Docker on macOS and rootless
                                  Apptainer on Linux when available.
  --mode local|internal|demo      Deployment profile. Default: local.
  --llm-provider NAME             openai-compatible, anthropic, google, ollama, or no-llm.
  --image IMAGE                   Published image tag or digest. Default: docker.io/deepmi/neurocade:latest.
  --build-from-source             Build Docker from this checkout and convert it
                                  to an Apptainer SIF. Requires Docker.
  --bridge-port PORT              Host bridge port. Default: 8765.
  --no-start                      Prepare required images without launching the app.
  --yes                           Noninteractive: preserve configured values and accept defaults.
  --help                          Show this help.
EOF
}

bootstrap_checkout() {
  for arg in "$@"; do
    case "$arg" in
      -h|--help)
        usage
        exit 0
        ;;
    esac
  done
  [[ -f "$SCRIPT_DIR/run.sh" ]] && return 0
  command -v curl >/dev/null 2>&1 || { echo "curl is required to download NeuroCade." >&2; exit 1; }
  command -v tar >/dev/null 2>&1 || { echo "tar is required to unpack NeuroCade." >&2; exit 1; }
  local install_dir="$DEFAULT_INSTALL_DIR"
  if [[ -t 0 && -t 1 ]]; then
    read -r -p "Install directory [$DEFAULT_INSTALL_DIR]: " install_dir
    install_dir="${install_dir:-$DEFAULT_INSTALL_DIR}"
  fi
  if [[ -d "$install_dir/.git" ]]; then
    exec bash "$install_dir/scripts/install.sh" "$@"
  fi
  if [[ -f "$install_dir/scripts/install.sh" ]]; then
    exec bash "$install_dir/scripts/install.sh" "$@"
  fi
  [[ ! -e "$install_dir" ]] || { echo "Install path exists and is not a NeuroCade checkout: $install_dir" >&2; exit 1; }
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$tmp_dir"
  mkdir -p "$(dirname "$install_dir")"
  mv "$tmp_dir"/* "$install_dir"
  rmdir "$tmp_dir"
  exec bash "$install_dir/scripts/install.sh" "$@"
}

is_tty() {
  # Prompts are invoked through command substitutions, so stdout is a pipe even
  # when the installer is attached to an interactive terminal.
  [[ -t 0 ]]
}

prompt() {
  local label="$1" default_value="${2:-}" secret="${3:-false}" value default_hint=""
  if [[ "${ASSUME_YES:-0}" -eq 1 || ! is_tty ]]; then
    printf '%s\n' "$default_value"
    return
  fi
  if [[ "$secret" == "true" ]]; then
    [[ -n "$default_value" ]] && default_hint=" [**existing key**]"
    read -r -s -p "$label${default_hint}: " value
    printf '\n' >&2
  else
    read -r -p "$label${default_value:+ [$default_value]}: " value
  fi
  printf '%s\n' "${value:-$default_value}"
}

env_line() {
  local value="${2:-}"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Environment values must fit on one line: $1" >&2
    return 1
  fi
  printf '%s=%s\n' "$1" "$value"
}

env_file_value() {
  local root="$1" key="$2"
  [[ -f "$root/.env" ]] || return 0
  sed -n "s/^${key}=//p" "$root/.env" | tail -n 1 | sed 's/^"//; s/"$//'
}

configured_or_default() {
  local root="$1" key="$2" default_value="${3:-}" current
  current="$(env_file_value "$root" "$key")"
  if [[ -n "$current" ]]; then
    printf '%s\n' "$current"
  elif [[ -n "${!key:-}" ]]; then
    printf '%s\n' "${!key}"
  else
    printf '%s\n' "$default_value"
  fi
}

detect_configured_provider() {
  local root="$1" configured candidate existing value
  configured="$(configured_or_default "$root" LLM_PROVIDER_DEFAULT "")"
  if [[ -n "$configured" ]]; then
    printf '%s\n' "$configured"
    return
  fi

  local -a candidates=()
  value="$(configured_or_default "$root" LLM_BACKEND_URL "")"
  [[ -n "$value" ]] && candidates+=(openai-compatible)
  value="$(configured_or_default "$root" LLM_BACKEND_API_KEY "")"
  [[ -n "$value" ]] && candidates+=(openai-compatible)
  value="$(configured_or_default "$root" ANTHROPIC_API_KEY "")"
  [[ -n "$value" ]] && candidates+=(anthropic)
  value="$(configured_or_default "$root" GOOGLE_API_KEY "")"
  [[ -n "$value" ]] && candidates+=(google)
  value="$(configured_or_default "$root" OLLAMA_BASE_URL "")"
  [[ -n "$value" ]] && candidates+=(ollama)

  local -a unique=()
  for candidate in "${candidates[@]+"${candidates[@]}"}"; do
    local found=0
    for existing in "${unique[@]+"${unique[@]}"}"; do
      if [[ "$existing" == "$candidate" ]]; then
        found=1
        break
      fi
    done
    (( found == 1 )) || unique+=("$candidate")
  done
  if (( ${#unique[@]} > 1 )); then
    echo "Multiple LLM provider configurations were found; pass --llm-provider explicitly." >&2
    return 2
  fi
  (( ${#unique[@]} == 0 )) || printf '%s\n' "${unique[0]}"
}

normalize_mode() {
  case "$1" in
    local|internal|demo) printf '%s\n' "$1" ;;
    *) echo "Invalid mode: $1" >&2; exit 2 ;;
  esac
}

normalize_provider() {
  case "$1" in
    openai-compatible|anthropic|google|ollama|no-llm) printf '%s\n' "$1" ;;
    *) echo "Invalid LLM provider: $1" >&2; exit 2 ;;
  esac
}

default_docker_platform() {
  local os arch
  os="$(uname -s)"
  arch="$(neurocade_host_arch)"
  if [[ "$os" == "Darwin" && "$arch" =~ ^(arm64|aarch64)$ ]]; then
    printf 'linux/amd64\n'
  fi
}

require_value() {
  local label="$1" value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "$label is required for the selected deployment mode." >&2
    exit 2
  fi
}

require_option_value() {
  local option="$1" value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    echo "$option requires a value." >&2
    exit 2
  fi
}

write_env() {
  local root="$1" mode="$2" provider="$3" runtime="$4" image_override="${5:-}" app_sif_mode="${6:-}" bridge_package="${7:-}" release_version="${8:-}" bridge_port="${9:-8765}" env_path
  env_path="$root/.env"
  local host_data_dir database_volume app_base_url app_bind app_port docker_platform image local_auth
  local clerk_publishable="" clerk_secret="" clerk_jwks="" clerk_issuer="" clerk_audience="" clerk_jwt_template=""
  host_data_dir="$(configured_or_default "$root" HOST_DATA_DIR "$root/neurocade-data")"
  if [[ "$host_data_dir" != /* ]]; then
    host_data_dir="$root/$host_data_dir"
  fi
  database_volume="$(configured_or_default "$root" NEUROCADE_DATABASE_VOLUME "neurocade-database")"
  docker_platform="$(configured_or_default "$root" NEUROCADE_DOCKER_PLATFORM "$(default_docker_platform)")"
  image="${image_override:-$(configured_or_default "$root" NEUROCADE_IMAGE "$DEFAULT_IMAGE")}"

  case "$mode" in
    local)
      app_base_url="$(configured_or_default "$root" APP_BASE_URL "http://localhost:8000")"
      app_bind="$(configured_or_default "$root" APP_HTTP_BIND "127.0.0.1")"
      app_port="$(configured_or_default "$root" APP_HTTP_PORT "8000")"
      local_auth="true"
      ;;
    internal)
      app_base_url="$(prompt "Application URL" "$(configured_or_default "$root" APP_BASE_URL "https://$(hostname 2>/dev/null || echo localhost)")")"
      app_bind="$(configured_or_default "$root" APP_HTTP_BIND "0.0.0.0")"
      app_port="$(configured_or_default "$root" APP_HTTP_PORT "8000")"
      local_auth="false"
      ;;
    demo)
      app_base_url="$(prompt "Public demo URL" "$(configured_or_default "$root" APP_BASE_URL "https://demo.neurocade.example.org")")"
      app_bind="$(configured_or_default "$root" APP_HTTP_BIND "0.0.0.0")"
      app_port="$(configured_or_default "$root" APP_HTTP_PORT "8000")"
      local_auth="false"
      ;;
  esac

  local llm_url="" llm_key="" llm_model="Qwen/Qwen3.6-35B-A3B" anthropic_key="" anthropic_model="" google_key="" google_model="" ollama_model="" ollama_base_url
  if [[ "$runtime" == "docker" ]]; then ollama_base_url="http://host.docker.internal:11434"; else ollama_base_url="http://127.0.0.1:11434"; fi
  case "$provider" in
    openai-compatible)
      llm_url="$(prompt "OpenAI-compatible base URL" "$(configured_or_default "$root" LLM_BACKEND_URL "")")"
      llm_key="$(prompt "OpenAI-compatible API key (optional)" "$(configured_or_default "$root" LLM_BACKEND_API_KEY "")" true)"
      llm_model="$(prompt "OpenAI-compatible model" "$(configured_or_default "$root" LLM_BACKEND_MODEL "$llm_model")")"
      require_value "OpenAI-compatible base URL" "$llm_url"
      ;;
    anthropic)
      anthropic_key="$(prompt "Anthropic API key" "$(configured_or_default "$root" ANTHROPIC_API_KEY "")" true)"
      anthropic_model="$(prompt "Anthropic model" "$(configured_or_default "$root" ANTHROPIC_MODEL "claude-3-5-sonnet-latest")")"
      require_value "Anthropic API key" "$anthropic_key"
      ;;
    google)
      google_key="$(prompt "Google API key" "$(configured_or_default "$root" GOOGLE_API_KEY "")" true)"
      google_model="$(prompt "Google model" "$(configured_or_default "$root" GOOGLE_MODEL "gemini-2.0-flash")")"
      require_value "Google API key" "$google_key"
      ;;
    ollama)
      ollama_model="$(prompt "Ollama model" "$(configured_or_default "$root" OLLAMA_MODEL "gemma4:e2b")")"
      llm_url="$ollama_base_url"
      llm_model="$ollama_model"
      ;;
    no-llm)
      llm_model="no-llm"
      ;;
  esac

  if [[ "$mode" != "local" ]]; then
    clerk_publishable="$(prompt "Clerk publishable key" "$(configured_or_default "$root" CLERK_PUBLISHABLE_KEY "")" true)"
    clerk_secret="$(prompt "Clerk secret key" "$(configured_or_default "$root" CLERK_SECRET_KEY "")" true)"
    clerk_jwks="$(prompt "Clerk JWKS URL" "$(configured_or_default "$root" CLERK_JWKS_URL "")")"
    clerk_issuer="$(prompt "Clerk issuer URL" "$(configured_or_default "$root" CLERK_ISSUER "")")"
    clerk_audience="$(prompt "Clerk audience" "$(configured_or_default "$root" CLERK_AUDIENCE "neurocade")")"
    clerk_jwt_template="$(prompt "Clerk JWT template name" "$(configured_or_default "$root" CLERK_JWT_TEMPLATE "$clerk_audience")")"
    require_value "Clerk publishable key" "$clerk_publishable"
    require_value "Clerk secret key" "$clerk_secret"
    require_value "Clerk JWKS URL" "$clerk_jwks"
    require_value "Clerk issuer URL" "$clerk_issuer"
    require_value "Clerk audience" "$clerk_audience"
    require_value "Clerk JWT template name" "$clerk_jwt_template"
  fi

  local app_host allowed_hosts
  app_host="${app_base_url#*://}"
  app_host="${app_host%%/*}"
  app_host="${app_host%%:*}"
  allowed_hosts="localhost,127.0.0.1"
  if [[ -n "$app_host" && "$app_host" != "localhost" && "$app_host" != "127.0.0.1" ]]; then
    allowed_hosts="$app_host,$allowed_hosts"
  fi
  mkdir -p "$host_data_dir/output"
  [[ -f "$env_path" ]] && cp "$env_path" "$env_path.backup.$(date +%Y%m%d%H%M%S)"

  {
    echo "# Generated by scripts/install.sh"
    env_line DEPLOYMENT_PROFILE "$mode"
    env_line APP_BASE_URL "$app_base_url"
    env_line APP_ALLOWED_HOSTS "$allowed_hosts"
    env_line APP_HTTP_BIND "$app_bind"
    env_line APP_HTTP_PORT "$app_port"
    env_line HOST_DATA_DIR "$host_data_dir"
    env_line NEUROCADE_DATABASE_VOLUME "$database_volume"
    env_line NEUROCADE_RUNTIME "$runtime"
    env_line NEUROCADE_BRIDGE_URL "http://127.0.0.1:$bridge_port"
    env_line NEUROCADE_BRIDGE_TOKEN_FILE "$root/.runtime/bridge-token"
    env_line NEUROCADE_BRIDGE_PORT "$bridge_port"
    env_line NEUROCADE_BRIDGE_PACKAGE "$bridge_package"
    env_line NEUROCADE_IMAGE "$image"
    env_line NEUROCADE_APP_SIF_MODE "$app_sif_mode"
    env_line NEUROCADE_RELEASE_VERSION "$release_version"
    env_line NEUROCADE_DOCKER_PLATFORM "$docker_platform"
    env_line NEUROCADE_GPU_MODE "$(configured_or_default "$root" NEUROCADE_GPU_MODE "auto")"
    env_line LOCAL_AUTH_ENABLED "$local_auth"
    env_line LOCAL_AUTH_USER_ID "local-user"
    env_line LOCAL_AUTH_EMAIL "local@example.com"
    env_line LOCAL_AUTH_NAME "Local User"
    env_line CLERK_PUBLISHABLE_KEY "$clerk_publishable"
    env_line CLERK_JWT_TEMPLATE "$clerk_jwt_template"
    env_line CLERK_SECRET_KEY "$clerk_secret"
    env_line CLERK_JWKS_URL "$clerk_jwks"
    env_line CLERK_ISSUER "$clerk_issuer"
    env_line CLERK_AUDIENCE "$clerk_audience"
    env_line LLM_PROVIDER_DEFAULT "$provider"
    env_line LLM_BACKEND_URL "$llm_url"
    env_line LLM_BACKEND_API_KEY "$llm_key"
    env_line LLM_BACKEND_MODEL "$llm_model"
    env_line OLLAMA_BASE_URL "$ollama_base_url"
    env_line OLLAMA_MODEL "$ollama_model"
    env_line ANTHROPIC_API_KEY "$anthropic_key"
    env_line ANTHROPIC_MODEL "$anthropic_model"
    env_line GOOGLE_API_KEY "$google_key"
    env_line GOOGLE_MODEL "$google_model"
  } >"$env_path"
  chmod 600 "$env_path"
}

bootstrap_checkout "$@"

MODE="local"
RUNTIME=""
LLM_PROVIDER=""
IMAGE_OVERRIDE=""
APP_SIF_MODE=""
BRIDGE_PACKAGE=""
RELEASE_VERSION=""
BUILD_FROM_SOURCE=0
BRIDGE_PORT="8765"
START=1
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      require_option_value "$1" "${2:-}"
      RUNTIME="$2"
      shift 2
      ;;
    --mode)
      require_option_value "$1" "${2:-}"
      MODE="$2"
      shift 2
      ;;
    --llm-provider)
      require_option_value "$1" "${2:-}"
      LLM_PROVIDER="$2"
      shift 2
      ;;
    --image)
      require_option_value "$1" "${2:-}"
      IMAGE_OVERRIDE="$2"
      shift 2
      ;;
    --build-from-source) BUILD_FROM_SOURCE=1; shift ;;
    --bridge-port)
      require_option_value "$1" "${2:-}"
      BRIDGE_PORT="$2"
      shift 2
      ;;
    --no-start) START=0; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/managed_python.sh"
source "$ROOT_DIR/scripts/lib/runtime_selection.sh"
source "$ROOT_DIR/scripts/lib/docker_cli.sh"
source "$ROOT_DIR/scripts/lib/apptainer_artifacts.sh"
configure_docker_cli_path
if [[ -z "$RUNTIME" ]]; then
  RUNTIME="$(configured_or_default "$ROOT_DIR" NEUROCADE_RUNTIME "")"
  if [[ -n "$RUNTIME" ]]; then
    echo "Using configured runtime: $RUNTIME"
  else
    RUNTIME="$(default_runtime)"
    echo "Selected runtime: $RUNTIME"
  fi
fi
validate_runtime "$RUNTIME" || exit 1
if [[ "$RUNTIME" == "apptainer" ]]; then
  if [[ "$BUILD_FROM_SOURCE" -eq 1 ]]; then
    command -v docker >/dev/null 2>&1 || {
      echo "Docker is required for --build-from-source. Remove the flag to install the latest release." >&2
      exit 1
    }
    APP_SIF_MODE="source"
  else
    APP_SIF_MODE="release"
  fi
elif [[ "$BUILD_FROM_SOURCE" -eq 1 ]]; then
  echo "--build-from-source is only valid with the Apptainer runtime." >&2
  exit 2
fi
[[ "$BRIDGE_PORT" =~ ^[0-9]+$ ]] && (( BRIDGE_PORT > 0 && BRIDGE_PORT < 65536 )) || { echo "Invalid bridge port: $BRIDGE_PORT" >&2; exit 2; }
MODE="$(normalize_mode "$MODE")"
if [[ -z "$LLM_PROVIDER" ]]; then
  LLM_PROVIDER="$(detect_configured_provider "$ROOT_DIR")"
  if [[ -n "$LLM_PROVIDER" ]]; then
    :
  elif [[ "$ASSUME_YES" -eq 1 ]] || ! is_tty; then
    LLM_PROVIDER="no-llm"
  else
    LLM_PROVIDER="$(prompt "LLM provider (openai-compatible, anthropic, google, ollama, or no-llm)" "no-llm")"
  fi
fi
LLM_PROVIDER="$(normalize_provider "$LLM_PROVIDER")"

if [[ "$ASSUME_YES" -eq 1 ]]; then
  echo "Noninteractive install: reusing configured values and accepting defaults."
fi

install_managed_uv
echo "Ensuring managed Python $NEUROCADE_PYTHON_VERSION..."
managed_uv python install "$NEUROCADE_PYTHON_VERSION"

if [[ "$APP_SIF_MODE" == "release" ]]; then
  python_bin="$(managed_python_path)"
  install_latest_apptainer_release "$ROOT_DIR" "$python_bin"
  BRIDGE_PACKAGE="$NEUROCADE_RESOLVED_BRIDGE_PACKAGE"
  RELEASE_VERSION="$NEUROCADE_RESOLVED_RELEASE_VERSION"
elif [[ "$APP_SIF_MODE" == "source" ]]; then
  BRIDGE_PACKAGE="$ROOT_DIR/packages/neurocade-runtime-tools"
fi

write_env "$ROOT_DIR" "$MODE" "$LLM_PROVIDER" "$RUNTIME" "$IMAGE_OVERRIDE" "$APP_SIF_MODE" "$BRIDGE_PACKAGE" "$RELEASE_VERSION" "$BRIDGE_PORT"

if [[ "$RUNTIME" == "docker" && -z "$IMAGE_OVERRIDE" ]]; then
  # Build the application from this checkout so the in-image bridge client and
  # the host bridge installed below always use the same protocol revision.
  "$ROOT_DIR/scripts/run.sh" build
elif [[ "$APP_SIF_MODE" == "source" ]]; then
  "$ROOT_DIR/scripts/build_sif.sh"
fi

if [[ "$START" -eq 1 ]]; then
  "$ROOT_DIR/scripts/run.sh" start -d
else
  "$ROOT_DIR/scripts/run.sh" prepare-tools
fi

echo
echo "NeuroCade setup complete."
echo "Application URL: $(env_file_value "$ROOT_DIR" APP_BASE_URL)"
echo "Useful commands:"
echo "  ./scripts/run.sh status"
echo "  ./scripts/run.sh logs"
echo "  ./scripts/run.sh stop"
