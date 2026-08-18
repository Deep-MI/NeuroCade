#!/usr/bin/env bash

doctor_ok() {
  printf 'OK    %s\n' "$1"
}

doctor_warn() {
  printf 'WARN  %s\n' "$1" >&2
}

doctor_fail() {
  printf 'FAIL  %s\n' "$1" >&2
  DOCTOR_FAILURES=$((DOCTOR_FAILURES + 1))
}

run_host_doctor() {
  DOCTOR_FAILURES=0
  local minimum_free_kb="${NEUROCADE_MIN_FREE_KB:-2097152}"
  local application_image_kb="${NEUROCADE_APP_IMAGE_SIZE_KB:-4194304}"
  if [[ ! "$application_image_kb" =~ ^[0-9]+$ ]]; then
    doctor_fail "NEUROCADE_APP_IMAGE_SIZE_KB must be a non-negative integer"
    application_image_kb=4194304
  fi
  if [[ -z "${NEUROCADE_MIN_FREE_KB:-}" ]]; then
    docker_image_exists || minimum_free_kb=$((minimum_free_kb + application_image_kb))
    [[ -f "$HOST_DATA_DIR/sif/deepmi_fastsurfer_cu128-v2.5.4-amd64.sif" ]] || minimum_free_kb=$((minimum_free_kb + 5390740))
    [[ -f "$HOST_DATA_DIR/sif/vnmd_dcm2niix_v1.0.20240202_20260512-amd64.sif" ]] || minimum_free_kb=$((minimum_free_kb + 40572))
  fi

  if command -v docker >/dev/null 2>&1; then
    doctor_ok "Docker is installed"
  else
    doctor_fail "Docker is required"
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    doctor_ok "Docker daemon is reachable"
  else
    doctor_fail "Docker daemon is not reachable by this user"
  fi
  if [[ "$(uname -m)" =~ ^(x86_64|amd64)$ ]]; then
    doctor_ok "Host architecture is amd64"
  else
    doctor_fail "This beta's pinned tool images require amd64; found $(uname -m)"
  fi

  local path available_kb
  for path in "$HOST_DATA_DIR" "$NEUROCADE_DB_DIR"; do
    if mkdir -p "$path" && [[ -w "$path" ]]; then
      doctor_ok "$path is writable"
    else
      doctor_fail "$path is not writable"
    fi
  done
  available_kb="$(df -Pk "$HOST_DATA_DIR" 2>/dev/null | awk 'NR == 2 {print $4}')"
  if [[ "$available_kb" =~ ^[0-9]+$ ]] && (( available_kb >= minimum_free_kb )); then
    doctor_ok "Disk has enough space for missing application and tool images"
  else
    doctor_fail "At least $(((minimum_free_kb + 1048575) / 1024 / 1024)) GiB free is required for missing application and tool images"
  fi

  if port_in_use "$HTTP_PORT"; then
    if docker ps --filter "name=^/${CONTAINER_NAME}$" --filter "status=running" --quiet 2>/dev/null | grep -q .; then
      doctor_ok "Port $HTTP_PORT is used by the running NeuroCade container"
    else
      doctor_warn "Port $HTTP_PORT is occupied; startup will select the next available port"
    fi
  else
    doctor_ok "Port $HTTP_PORT is available"
  fi

  case "${LLM_PROVIDER_DEFAULT:-no-llm}" in
    no-llm)
      doctor_ok "Assistant is intentionally disabled"
      ;;
    openai-compatible)
      if [[ -n "${LLM_BACKEND_URL:-}" ]]; then
        doctor_ok "OpenAI-compatible provider is configured"
        if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 "${LLM_BACKEND_URL%/}/v1/models" >/dev/null 2>&1; then
          doctor_ok "OpenAI-compatible provider is reachable"
        else
          doctor_warn "OpenAI-compatible provider is configured but not currently reachable"
        fi
      else
        doctor_warn "OpenAI-compatible provider is selected but LLM_BACKEND_URL is empty"
      fi
      ;;
    anthropic)
      if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        doctor_warn "Anthropic is selected but ANTHROPIC_API_KEY is empty"
      else
        doctor_ok "Anthropic provider is configured"
        if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 \
          -H "x-api-key: ${ANTHROPIC_API_KEY}" \
          -H "anthropic-version: 2023-06-01" \
          "https://api.anthropic.com/v1/models" >/dev/null 2>&1; then
          doctor_ok "Anthropic provider is reachable"
        else
          doctor_warn "Anthropic provider is configured but not currently reachable"
        fi
      fi
      ;;
    google)
      if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
        doctor_warn "Google is selected but GOOGLE_API_KEY is empty"
      else
        doctor_ok "Google provider is configured"
        if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 \
          -H "x-goog-api-key: ${GOOGLE_API_KEY}" \
          "https://generativelanguage.googleapis.com/v1beta/models" >/dev/null 2>&1; then
          doctor_ok "Google provider is reachable"
        else
          doctor_warn "Google provider is configured but not currently reachable"
        fi
      fi
      ;;
    ollama)
      if [[ -z "${OLLAMA_BASE_URL:-}" ]]; then
        doctor_warn "Ollama is selected but OLLAMA_BASE_URL is empty"
      elif command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 "${OLLAMA_BASE_URL%/}/api/tags" >/dev/null 2>&1; then
        doctor_ok "Ollama is reachable"
      else
        doctor_warn "Ollama is configured but not currently reachable"
      fi
      ;;
    *) doctor_fail "Unknown LLM_PROVIDER_DEFAULT=${LLM_PROVIDER_DEFAULT}" ;;
  esac

  if (( DOCTOR_FAILURES > 0 )); then
    printf 'Doctor found %d fatal problem(s).\n' "$DOCTOR_FAILURES" >&2
    return 1
  fi
  doctor_ok "Host preflight passed"
}
