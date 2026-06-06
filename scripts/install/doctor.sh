#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer doctor workflow.


doctor_check_command() {
  local label="$1"
  local command_name="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    printf '  [ok]   %-18s %s\n' "$label" "$(command -v "$command_name")"
  else
    printf '  [miss] %-18s not found\n' "$label"
  fi
}

doctor_check_node() {
  prepend_local_node "$1"
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    printf '  [ok]   %-18s %s / npm %s\n' "Node.js" "$(node -v)" "$(npm -v)"
  else
    printf '  [plan] %-18s repo-local Node.js v22 install\n' "Node.js"
  fi
}

doctor_check_uv() {
  local uv_path
  uv_path="$(find_uv || true)"
  if [[ -n "$uv_path" ]]; then
    printf '  [ok]   %-18s %s\n' "uv" "$uv_path"
  else
    printf '  [plan] %-18s install uv, then create .venv from pyproject.toml\n' "uv"
  fi
}

doctor_check_apptainer() {
  local root="$1"
  prepend_local_lima "$root"
  local configured_apptainer
  configured_apptainer="$(env_file_value "$root" APPTAINER_BIN)"
  if [[ -n "$configured_apptainer" && -x "$configured_apptainer" ]]; then
    APPTAINER_BIN="$configured_apptainer"
  fi
  if command -v "${APPTAINER_BIN:-apptainer}" >/dev/null 2>&1; then
    printf '  [ok]   %-18s %s\n' "Apptainer" "$(command -v "${APPTAINER_BIN:-apptainer}")"
    return
  fi
  case "$(uname -s 2>/dev/null || true)" in
    Linux)
      printf '  [plan] %-18s repo-local unprivileged Apptainer install\n' "Apptainer"
      ;;
    Darwin)
      if command -v limactl >/dev/null 2>&1; then
        printf '  [plan] %-18s Lima-backed Apptainer wrapper using existing limactl\n' "Apptainer"
      elif command -v brew >/dev/null 2>&1; then
        printf '  [plan] %-18s install Lima with Homebrew, then Apptainer in Lima\n' "Apptainer"
      else
        printf '  [plan] %-18s repo-local Lima binary, then Apptainer in Lima\n' "Apptainer"
      fi
      ;;
    *)
      printf '  [miss] %-18s unsupported OS\n' "Apptainer"
      ;;
  esac
}

doctor_check_env_secret() {
  local root="$1"
  local key="$2"
  local value
  value="$(env_file_value "$root" "$key")"
  if [[ -n "$value" && "$value" != "CHANGE_ME" && "$value" != "fastsurfer-dev-redis" ]]; then
    printf '  [ok]   %-22s configured\n' "$key"
  else
    printf '  [warn] %-22s missing or default\n' "$key"
  fi
}

doctor_check_file() {
  local label="$1"
  local path="$2"
  if [[ -z "$path" ]]; then
    printf '  [warn] %-22s path is not configured\n' "$label"
    return
  fi
  if [[ -e "$path" ]]; then
    printf '  [ok]   %-22s %s\n' "$label" "$path"
  else
    printf '  [warn] %-22s missing: %s\n' "$label" "$path"
  fi
}

doctor_check_directory_writable() {
  local label="$1"
  local path="$2"
  mkdir -p "$path" >/dev/null 2>&1 || true
  if [[ -d "$path" && -w "$path" ]]; then
    printf '  [ok]   %-22s writable\n' "$label"
  else
    printf '  [warn] %-22s not writable: %s\n' "$label" "$path"
  fi
}

run_doctor() {
  local root="$1"
  local mode="${2:-${MODE:-local}}"
  local provider="${3:-${LLM_PROVIDER:-openai-compatible}}"

  echo "NeuroCade installer doctor"
  echo "Repository: $root"
  echo "Mode: $mode"
  echo "LLM provider: $provider"
  echo
  echo "Host tools"
  doctor_check_command "git" git
  doctor_check_command "curl" curl
  doctor_check_uv
  doctor_check_node "$root"
  doctor_check_apptainer "$root"
  echo
  echo "Deployment checks"
  printf '  [info] %-22s %s\n' "Profile" "$mode"
  if [[ "$mode" != "local" ]]; then
    for key in VITE_CLERK_PUBLISHABLE_KEY CLERK_SECRET_KEY CLERK_JWKS_URL CLERK_ISSUER CLERK_AUDIENCE; do
      if [[ -n "$(env_file_value "$root" "$key")" ]]; then
        printf '  [ok]   %-22s configured\n' "$key"
      else
        printf '  [need] %-22s required for %s\n' "$key" "$mode"
      fi
    done
  fi
  doctor_check_env_secret "$root" POSTGRES_PASSWORD
  doctor_check_env_secret "$root" REDIS_PASSWORD
  doctor_check_directory_writable "Data directory" "$root/neurocade-data"
  local tool_catalog_path container_inventory_path
  tool_catalog_path="$(env_file_value "$root" NEUROCADE_INSTALLED_TOOLS_JSONL)"
  container_inventory_path="$(env_file_value "$root" NEUROCADE_CONTAINER_INVENTORY)"
  doctor_check_file "Installed tool index" "${tool_catalog_path:-$root/llm-data/tool-catalog/installed_tools.jsonl}"
  doctor_check_file "Container inventory" "${container_inventory_path:-$root/llm-data/tool-catalog/installed_containers.json}"
  if [[ "$mode" == "local" ]]; then
    printf '  [ok]   %-22s Vite dev server allowed\n' "Client serving"
  elif [[ -d "$root/client/dist" ]]; then
    printf '  [ok]   %-22s built static client\n' "Client serving"
  else
    printf '  [need] %-22s run npm --prefix client run build\n' "Client serving"
  fi
  if [[ "$mode" != "local" && -z "$(env_file_value "$root" MONITORING_ADMIN_USER_IDS)" ]]; then
    printf '  [need] %-22s configure admin user IDs\n' "Monitoring admins"
  fi
  echo
  echo "Runtime network containment"
  echo "  - container commands are built with --net --network none"
  echo "  - deny host egress for api-worker with your firewall, then verify:"
  echo "    sudo -n true >/dev/null 2>&1 && sudo -u \"$(id -un)\" curl --max-time 3 https://example.org || true"
  echo
  echo "Actions the installer may take"
  echo "  - write $root/.env with shell-safe quoted values"
  echo "  - write install log to $root/.runtime/logs/install.log"
  echo "  - create $root/neurocade-data/output"
  echo "  - install uv, then create $root/.venv from pyproject.toml"
  echo "  - download Apptainer/Lima/Node only when missing and --no-prereqs is not set"
  echo "  - skip demo data unless --with-demo-case is used or an interactive user opts in"
  echo "  - download the release demo/sample case before attempting a local FastSurfer build"
  if [[ -n "${NEUROCADE_VERSION_CHECK_URL:-}" ]]; then
    echo "  - run update checks for NeuroCade at app startup and every 24 hours while running"
  else
    echo "  - update checks use the default version endpoint and stay quiet when unreachable"
  fi
}
