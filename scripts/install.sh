#!/usr/bin/env bash
# NeuroCade matched-runtime installer.
set -euo pipefail

ARCHIVE_URL="${NEUROCADE_ARCHIVE_URL:-https://github.com/Deep-MI/NeuroCade/archive/refs/heads/main.tar.gz}"
RELEASE_MANIFEST_URL="${NEUROCADE_RELEASE_MANIFEST_URL:-https://github.com/Deep-MI/NeuroCade/releases/latest/download/neurocade-release.json}"
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
  --image IMAGE                   Published image tag or digest. Docker only.
                                  When omitted, Docker builds this checkout under
                                  an installation-specific local image tag.
  --version stable|beta|TAG       Apptainer release channel or exact v-prefixed tag.
                                  Default: stable, falling back to the newest compatible release.
  --build-from-source             Build Docker from this checkout and convert it
                                  to an Apptainer SIF. Requires Docker.
  --bridge-port PORT              Host bridge port. Default: 8765.
  --no-start                      Prepare required images without launching the app.
  --yes                           Noninteractive: preserve configured values and accept defaults.
  --help                          Show this help.
EOF
}

bootstrap_release_source() {
  local tmp_dir="$1" manifest release_values tag source checksum base digest expected
  manifest="$tmp_dir/neurocade-release.json"
  curl --fail --location --silent --show-error --retry 4 --retry-all-errors -o "$manifest" "$RELEASE_MANIFEST_URL"
  release_values="$(python3 -c 'import json,re,sys; p=json.load(open(sys.argv[1])); a=p.get("source_archive",{}); ok=lambda v: isinstance(v,str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*",v); assert p.get("schema_version")==2 and re.fullmatch(r"v[0-9][A-Za-z0-9._-]*",p.get("tag","")) and ok(a.get("filename")) and ok(a.get("sha256_filename")) and a["sha256_filename"]==a["filename"]+".sha256"; print(p["tag"],a["filename"],a["sha256_filename"],sep="\n")' "$manifest")" || {
    echo "The latest NeuroCade release does not support verified source installation." >&2; exit 1;
  }
  tag="$(printf '%s\n' "$release_values" | sed -n '1p')"
  source="$(printf '%s\n' "$release_values" | sed -n '2p')"
  checksum="$(printf '%s\n' "$release_values" | sed -n '3p')"
  base="https://github.com/Deep-MI/NeuroCade/releases/download/$tag"
  curl --fail --location --silent --show-error --retry 4 --retry-all-errors -o "$tmp_dir/$source" "$base/$source"
  curl --fail --location --silent --show-error --retry 4 --retry-all-errors -o "$tmp_dir/$checksum" "$base/$checksum"
  expected="$(awk 'NR == 1 {print tolower($1)}' "$tmp_dir/$checksum")"
  if command -v sha256sum >/dev/null 2>&1; then digest="$(sha256sum "$tmp_dir/$source" | awk '{print $1}')"; else digest="$(shasum -a 256 "$tmp_dir/$source" | awk '{print $1}')"; fi
  [[ "$expected" =~ ^[0-9a-f]{64}$ && "$digest" == "$expected" ]] || { echo "NeuroCade source checksum verification failed." >&2; exit 1; }
  python3 -c 'import pathlib,sys,tarfile; a,d=sys.argv[1:]; t=tarfile.open(a,"r:gz"); m=t.getmembers(); roots=set();
for x in m:
 p=pathlib.PurePosixPath(x.name); assert p.parts and not p.is_absolute() and ".." not in p.parts and not x.isdev() and not x.isfifo() and not x.islnk() and not x.issym(); roots.add(p.parts[0])
assert len(roots)==1; t.extractall(d); print(pathlib.Path(d)/roots.pop())' "$tmp_dir/$source" "$tmp_dir"
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
  command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required to verify NeuroCade releases." >&2; exit 1; }
  local install_dir="$DEFAULT_INSTALL_DIR"
  if [[ -t 0 && -t 1 ]]; then
    if ! read -r -p "Install directory [$DEFAULT_INSTALL_DIR]: " install_dir; then
      install_dir=""
    fi
    install_dir="${install_dir:-$DEFAULT_INSTALL_DIR}"
  fi
  echo "Installing NeuroCade to $install_dir"
  if [[ -d "$install_dir/.git" ]]; then
    exec bash "$install_dir/scripts/install.sh" "$@"
  fi
  if [[ -f "$install_dir/scripts/install.sh" && -f "$install_dir/.runtime/runtime-root-owned" ]]; then
    local update_tmp source_root
    update_tmp="$(mktemp -d)"
    source_root="$(bootstrap_release_source "$update_tmp")"
    set +e
    env NEUROCADE_INSTALL_DIR="$install_dir" bash "$source_root/scripts/update.sh" \
      --bootstrap-staged "$update_tmp/$(basename "$(find "$update_tmp" -maxdepth 1 -name 'neurocade-source-*.tar.gz' -print -quit)")" \
      "$update_tmp/neurocade-release.json" --yes -- "$@"
    local update_status=$?
    rm -rf "$update_tmp"
    exit "$update_status"
  elif [[ -f "$install_dir/scripts/install.sh" ]]; then
    echo "Existing NeuroCade directory is not marked as installer-owned: $install_dir" >&2
    exit 1
  fi
  if [[ -L "$install_dir" || ( -e "$install_dir" && ! -d "$install_dir" ) ]]; then
    echo "Install path exists and is not a directory: $install_dir" >&2
    exit 1
  fi
  local existing_empty_dir=0
  if [[ -d "$install_dir" ]]; then
    if [[ -n "$(find "$install_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      echo "Install path exists and is not an empty directory or NeuroCade checkout: $install_dir" >&2
      exit 1
    fi
    existing_empty_dir=1
  fi
  local tmp_dir source_root
  tmp_dir="$(mktemp -d)"
  if [[ -n "${NEUROCADE_ARCHIVE_URL+x}" ]]; then
    curl --fail --location --silent --show-error --retry 4 --retry-all-errors "$ARCHIVE_URL" | tar -xz -C "$tmp_dir"
    source_root="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d -print -quit)"
  else
    source_root="$(bootstrap_release_source "$tmp_dir")"
  fi
  mkdir -p "$(dirname "$install_dir")"
  if [[ "$existing_empty_dir" -eq 1 ]]; then
    rmdir "$install_dir"
  fi
  mv "$source_root" "$install_dir"
  local bootstrap_values bootstrap_version bootstrap_revision
  bootstrap_values="$(python3 "$install_dir/scripts/release/release_manifest.py" read-update "$tmp_dir/neurocade-release.json" 2>/dev/null || true)"
  bootstrap_version="$(printf '%s\n' "$bootstrap_values" | sed -n '2p')"
  bootstrap_revision="$(printf '%s\n' "$bootstrap_values" | sed -n '3p')"
  rm -rf "$tmp_dir"
  mkdir -p "$install_dir/.runtime"
  : >"$install_dir/.runtime/runtime-root-owned"
  exec env NEUROCADE_INSTALL_VERSION="${bootstrap_version:-development}" \
    NEUROCADE_INSTALL_REVISION="${bootstrap_revision:-unknown}" NEUROCADE_INSTALL_CHANNEL=stable \
    bash "$install_dir/scripts/install.sh" "$@"
}

is_tty() {
  # Prompts are invoked through command substitutions, so stdout is a pipe even
  # when the installer is attached to an interactive terminal.
  [[ -t 0 ]]
}

prompt() {
  local label="$1" default_value="${2:-}" secret="${3:-false}" value default_hint=""
  if [[ "${ASSUME_YES:-0}" -eq 1 ]] || ! is_tty; then
    printf '%s\n' "$default_value"
    return
  fi
  if [[ "$secret" == "true" ]]; then
    [[ -n "$default_value" ]] && default_hint=" [**existing key**]"
    if ! read -r -s -p "$label${default_hint}: " value; then
      value=""
    fi
    printf '\n' >&2
  else
    if ! read -r -p "$label${default_value:+ [$default_value]}: " value; then
      value=""
    fi
  fi
  printf '%s\n' "${value:-$default_value}"
}

env_file_value() {
  local root="$1" key="$2"
  [[ -f "$root/.env" ]] || return 0
  local value
  value="$(sed -n "s/^${key}=//p" "$root/.env" | tail -n 1)"
  decode_env_value "$value"
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

local_docker_image() {
  local install_id="$1"
  printf 'neurocade:local-%s\n' "${install_id:0:12}"
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
  local data_dir_was_present=0
  [[ -e "$host_data_dir" ]] && data_dir_was_present=1
  mkdir -p "$host_data_dir/output"
  if [[ "$data_dir_was_present" -eq 0 ]]; then
    printf '%s\n' "$INSTALL_ID" >"$host_data_dir/.neurocade-install-id"
  fi
  if [[ -f "$env_path" ]]; then
    if [[ ! -f "$root/.runtime/preinstall-env" ]]; then
      cp "$env_path" "$root/.runtime/preinstall-env"
    fi
  fi

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
    env_line NEUROCADE_VERSION "${NEUROCADE_INSTALL_VERSION:-$(configured_or_default "$root" NEUROCADE_VERSION "development")}"
    env_line NEUROCADE_SOURCE_REVISION "${NEUROCADE_INSTALL_REVISION:-$(configured_or_default "$root" NEUROCADE_SOURCE_REVISION "unknown")}"
    env_line NEUROCADE_UPDATE_CHANNEL "${NEUROCADE_INSTALL_CHANNEL:-$(configured_or_default "$root" NEUROCADE_UPDATE_CHANNEL "stable")}"
    env_line NEUROCADE_ARTIFACT_IDENTITY "${NEUROCADE_INSTALL_ARTIFACT:-$(configured_or_default "$root" NEUROCADE_ARTIFACT_IDENTITY "$image")}"
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
  cp "$env_path" "$root/.runtime/installed-env"
  chmod 600 "$root/.runtime/installed-env"
}

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

bootstrap_checkout "$@"

MODE="local"
RUNTIME=""
LLM_PROVIDER=""
IMAGE_OVERRIDE=""
RELEASE_SELECTOR=""
APP_SIF_MODE=""
BRIDGE_PACKAGE=""
RELEASE_VERSION=""
BUILD_FROM_SOURCE=0
BUILD_DOCKER_IMAGE=1
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
      BUILD_DOCKER_IMAGE=0
      shift 2
      ;;
    --version)
      require_option_value "$1" "${2:-}"
      RELEASE_SELECTOR="$2"
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
if [[ ! -e "$ROOT_DIR/.runtime" ]]; then
  mkdir -p "$ROOT_DIR/.runtime"
  : >"$ROOT_DIR/.runtime/runtime-root-owned"
else
  mkdir -p "$ROOT_DIR/.runtime"
fi
INSTALL_ID_FILE="$ROOT_DIR/.runtime/install-id"
if [[ ! -s "$INSTALL_ID_FILE" ]]; then
  umask 077
  od -An -N16 -tx1 /dev/urandom | tr -d ' \n' >"$INSTALL_ID_FILE"
  printf '\n' >>"$INSTALL_ID_FILE"
fi
INSTALL_ID="$(sed -n '1p' "$INSTALL_ID_FILE")"
source "$ROOT_DIR/scripts/lib/managed_python.sh"
source "$ROOT_DIR/scripts/lib/runtime_selection.sh"
source "$ROOT_DIR/scripts/lib/docker_cli.sh"
source "$ROOT_DIR/scripts/lib/apptainer_artifacts.sh"
source "$ROOT_DIR/scripts/lib/env.sh"
source "$ROOT_DIR/scripts/lib/provenance.sh"
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
  if [[ -n "$IMAGE_OVERRIDE" ]]; then
    echo "--image only applies to --runtime docker; use --version for Apptainer releases." >&2
    exit 2
  fi
  if [[ "$BUILD_FROM_SOURCE" -eq 1 ]]; then
    if [[ -n "$RELEASE_SELECTOR" ]]; then
      echo "--version cannot be combined with --build-from-source." >&2
      exit 2
    fi
    command -v docker >/dev/null 2>&1 || {
      echo "Docker is required for --build-from-source. Remove the flag to install the latest release." >&2
      exit 1
    }
    APP_SIF_MODE="source"
  else
    APP_SIF_MODE="release"
  fi
elif [[ -n "$RELEASE_SELECTOR" ]]; then
  echo "--version only applies to --runtime apptainer; use --image for Docker images." >&2
  exit 2
elif [[ "$BUILD_FROM_SOURCE" -eq 1 ]]; then
  echo "--build-from-source is only valid with the Apptainer runtime." >&2
  exit 2
fi
[[ "$RUNTIME" == "docker" ]] || BUILD_DOCKER_IMAGE=0
if [[ "$BUILD_DOCKER_IMAGE" -eq 1 ]]; then
  IMAGE_OVERRIDE="$(local_docker_image "$INSTALL_ID")"
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
managed_uv python install --no-bin "$NEUROCADE_PYTHON_VERSION"

if [[ "$APP_SIF_MODE" == "release" ]]; then
  python_bin="$(managed_python_path)"
  install_latest_apptainer_release "$ROOT_DIR" "$python_bin" "${RELEASE_SELECTOR:-stable}"
  BRIDGE_PACKAGE="$NEUROCADE_RESOLVED_BRIDGE_PACKAGE"
  RELEASE_VERSION="$NEUROCADE_RESOLVED_RELEASE_VERSION"
elif [[ "$APP_SIF_MODE" == "source" ]]; then
  BRIDGE_PACKAGE="$ROOT_DIR/packages/neurocade-runtime-tools"
fi

if [[ -z "${NEUROCADE_INSTALL_REVISION:-}" ]] && [[ -d "$ROOT_DIR/.git" ]]; then
  NEUROCADE_INSTALL_REVISION="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  export NEUROCADE_INSTALL_REVISION
fi
if [[ -z "${NEUROCADE_INSTALL_VERSION:-}" && -n "$RELEASE_VERSION" ]]; then
  NEUROCADE_INSTALL_VERSION="$RELEASE_VERSION"
  export NEUROCADE_INSTALL_VERSION
fi

write_env "$ROOT_DIR" "$MODE" "$LLM_PROVIDER" "$RUNTIME" "$IMAGE_OVERRIDE" "$APP_SIF_MODE" "$BRIDGE_PACKAGE" "$RELEASE_VERSION" "$BRIDGE_PORT"

if [[ "$BUILD_DOCKER_IMAGE" -eq 1 ]]; then
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

installed_version="$(env_file_value "$ROOT_DIR" NEUROCADE_VERSION)"
installed_revision="$(env_file_value "$ROOT_DIR" NEUROCADE_SOURCE_REVISION)"
installed_channel="$(env_file_value "$ROOT_DIR" NEUROCADE_UPDATE_CHANNEL)"
installed_artifact="$(env_file_value "$ROOT_DIR" NEUROCADE_ARTIFACT_IDENTITY)"
if [[ "$installed_revision" == unknown ]] && command -v git >/dev/null 2>&1 && [[ -d "$ROOT_DIR/.git" ]]; then
  installed_revision="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
fi
write_provenance "$ROOT_DIR" "$installed_version" "$installed_revision" "$installed_channel" "$installed_artifact"
"$(managed_python_path)" "$ROOT_DIR/scripts/update_source.py" write-manifest "$ROOT_DIR" "$ROOT_DIR/.runtime/source-manifest.json"

echo
echo "NeuroCade setup complete."
echo "Application URL: $(env_file_value "$ROOT_DIR" APP_BASE_URL)"
echo "Useful commands:"
echo "  ./scripts/run.sh status"
echo "  ./scripts/run.sh logs"
echo "  ./scripts/run.sh stop"
