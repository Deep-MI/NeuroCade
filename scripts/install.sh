#!/usr/bin/env bash
# Purpose:
#   Runs the NeuroCade install helper workflow.

set -euo pipefail

APP_DISPLAY_NAME="NeuroCade"
REPO_URL="${NEUROCADE_REPO_URL:-https://github.com/Deep-MI/NeuroCade.git}"
DEFAULT_INSTALL_DIR="${NEUROCADE_INSTALL_DIR:-$HOME/NeuroCade}"
DEFAULT_RELEASE_CHANNEL="${NEUROCADE_INSTALL_CHANNEL:-stable}"
RELEASE_CONTAINER_BASE_URL="https://github.com/Deep-MI/NeuroCade/releases"
SAMPLE_CASE_ARTIFACT_NAME="${NEUROCADE_SAMPLE_CASE_ARTIFACT_NAME:-neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz}"
REQUIRED_RUNTIME_ASSET_NAME="${NEUROCADE_REQUIRED_RUNTIME_ASSET_NAME:-bash-image-python-3.12.sif}"
LOCAL_APPTAINER_DIR_REL=".apptainer/runtime"
LOCAL_LIMA_DIR_REL=".lima"
LOCAL_NODE_DIR_REL=".node"
FREESURFER_LICENSE_URL="https://surfer.nmr.mgh.harvard.edu/registration.html"
SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"
INSTALL_LIB_DIR="$SCRIPT_DIR/install"

usage() {
  cat <<'EOF'
NeuroCade interactive installer

Quick install from a terminal:
  bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh)

From an existing checkout:
  ./scripts/install.sh

Options:
  --release-channel stable|prerelease|dev
                                  Fresh one-line installs clone latest stable by default.
  --stable                        Alias for --release-channel stable.
  --prerelease                    Clone the latest prerelease tag for fresh one-line installs.
  --dev                           Clone the repository default branch for fresh one-line installs.
  --mode local|internal|demo      Deployment profile. If omitted, prompts interactively.
  --llm-provider NAME             openai-compatible, anthropic, google, or ollama.
  --no-start                      Write configuration but do not start the Apptainer stack.
  --no-prereqs                    Do not install missing prerequisites such as uv, Node.js, Lima, or Apptainer.
  --desktop                       Prepare the local Electron desktop launcher.
  --no-desktop                    Skip Electron desktop setup.
  --with-freesurfer               Download and install the full licensed FreeSurfer runtime image.
  --dry-run                       Show planned actions without writing files or starting services.
  --doctor                        Check host readiness and show the installer plan.
  --yes                           Accept defaults for omitted prompts.
  --help                          Show this help.

Deployment modes:
  local      Single user on this machine, local auth enabled, localhost binding.
  internal   Authenticated institutional server for de-identified research MRI.
  demo       Public sample-data instance. Uploads and destructive actions are disabled.
EOF
}

normalize_release_channel() {
  local value="$1"
  case "$value" in
    ""|stable)
      printf 'stable\n'
      ;;
    prerelease|pre-release|pre|beta)
      printf 'prerelease\n'
      ;;
    dev|main)
      printf 'dev\n'
      ;;
    *)
      echo "Invalid release channel: $value. Use stable, prerelease, or dev." >&2
      exit 2
      ;;
  esac
}

remote_release_tags() {
  git ls-remote --tags --refs "$REPO_URL" "v*" | sed -n 's#.*refs/tags/##p'
}

stable_release_tags_desc() {
  remote_release_tags | sed -En '
    s/^v([0-9]+)\.([0-9]+)\.([0-9]+)$/\1 \2 \3 0 &/p
    s/^v([0-9]+)\.([0-9]+)\.([0-9]+)-([0-9]+)$/\1 \2 \3 \4 &/p
  ' | sort -k1,1nr -k2,2nr -k3,3nr -k4,4nr | awk '{print $5}'
}

prerelease_tags_desc() {
  remote_release_tags | sed -En '
    s/^v([0-9]+)\.([0-9]+)\.([0-9]+)-beta\.([0-9]+)$/\1 \2 \3 \4 &/p
  ' | sort -k1,1nr -k2,2nr -k3,3nr -k4,4nr | awk '{print $5}'
}

release_asset_url_available() {
  local url="$1"
  curl -fsIL "$url" >/dev/null 2>&1
}

required_release_asset_available_for_tag() {
  local tag="$1"
  local filename="${2:-$REQUIRED_RUNTIME_ASSET_NAME}"
  release_asset_url_available "$(neurocade_release_asset_url_for_tag "$tag" "$filename")"
}

latest_release_tag_with_required_asset() {
  local listing_function="$1"
  local tag url
  while IFS= read -r tag; do
    [[ -n "$tag" ]] || continue
    if required_release_asset_available_for_tag "$tag"; then
      printf '%s\n' "$tag"
      return 0
    fi
    url="$(neurocade_release_asset_url_for_tag "$tag" "$REQUIRED_RUNTIME_ASSET_NAME")"
    echo "Skipping NeuroCade release $tag because required runtime asset is unavailable: $url" >&2
  done < <("$listing_function" || true)
  return 0
}

latest_stable_tag() {
  latest_release_tag_with_required_asset stable_release_tags_desc
}

latest_prerelease_tag() {
  latest_release_tag_with_required_asset prerelease_tags_desc
}

selected_release_ref() {
  local channel="$1"
  case "$channel" in
    stable)
      latest_stable_tag
      ;;
    prerelease)
      latest_prerelease_tag
      ;;
    dev)
      printf '\n'
      ;;
  esac
}

current_release_tag() {
  local root="$1"
  if command -v git >/dev/null 2>&1 && [[ -d "$root/.git" ]]; then
    git -C "$root" describe --tags --exact-match HEAD 2>/dev/null || true
  fi
}

configure_container_release_tag() {
  local root="$1"
  if [[ -n "${NEUROCADE_CONTAINER_RELEASE_TAG:-}" ]]; then
    return 0
  fi

  local release_tag
  release_tag="$(current_release_tag "$root")"
  if [[ -n "$release_tag" ]]; then
    export NEUROCADE_CONTAINER_RELEASE_TAG="$release_tag"
    return 0
  fi

  if [[ "${INSTALL_CHANNEL:-}" == "dev" ]]; then
    release_tag="$(latest_prerelease_tag || true)"
    if [[ -n "$release_tag" ]]; then
      export NEUROCADE_CONTAINER_RELEASE_TAG="$release_tag"
      echo "Using latest prerelease assets: $release_tag"
    else
      echo "Could not resolve latest prerelease assets; falling back to latest release assets." >&2
    fi
  fi
}

neurocade_release_asset_url() {
  local filename="$1"
  local tag="${NEUROCADE_CONTAINER_RELEASE_TAG:-latest}"
  if [[ -z "$tag" || "$tag" == "latest" ]]; then
    printf '%s/latest/download/%s\n' "${RELEASE_CONTAINER_BASE_URL%/}" "$filename"
  else
    printf '%s/download/%s/%s\n' "${RELEASE_CONTAINER_BASE_URL%/}" "$tag" "$filename"
  fi
}

neurocade_release_asset_url_for_tag() {
  local tag="$1"
  local filename="$2"
  printf '%s/download/%s/%s\n' "${RELEASE_CONTAINER_BASE_URL%/}" "$tag" "$filename"
}

bootstrap_from_raw_script() {
  [[ -f "$INSTALL_LIB_DIR/common.sh" ]] && return 0
  local release_channel="$DEFAULT_RELEASE_CHANNEL"
  local forwarded_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --release-channel)
        if [[ $# -lt 2 ]]; then
          echo "--release-channel requires one of: stable, prerelease, dev" >&2
          exit 2
        fi
        release_channel="$(normalize_release_channel "${2:-}")"
        shift 2
        ;;
      --stable)
        release_channel="stable"
        shift
        ;;
      --prerelease)
        release_channel="prerelease"
        shift
        ;;
      --dev)
        release_channel="dev"
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        forwarded_args+=("$1")
        shift
        ;;
    esac
  done

  if ! command -v git >/dev/null 2>&1; then
    echo "git is required for the one-line installer bootstrap." >&2
    exit 1
  fi

  local install_dir="$DEFAULT_INSTALL_DIR"
  if [[ -t 0 && -t 1 ]]; then
    read -r -p "Install directory [$DEFAULT_INSTALL_DIR]: " install_dir
    install_dir="${install_dir:-$DEFAULT_INSTALL_DIR}"
  fi
  if [[ -d "$install_dir/.git" ]]; then
    if (( ${#forwarded_args[@]} > 0 )); then
      NEUROCADE_INSTALL_CHANNEL="$release_channel" exec bash "$install_dir/scripts/install.sh" "${forwarded_args[@]}"
    fi
    NEUROCADE_INSTALL_CHANNEL="$release_channel" exec bash "$install_dir/scripts/install.sh"
  fi
  if [[ -e "$install_dir" ]]; then
    echo "Install path exists but is not a git checkout: $install_dir" >&2
    exit 1
  fi
  release_channel="$(normalize_release_channel "$release_channel")"
  local release_ref=""
  release_ref="$(selected_release_ref "$release_channel")"
  if [[ "$release_channel" != "dev" && -z "$release_ref" ]]; then
    echo "Could not resolve a latest $release_channel NeuroCade release tag from $REPO_URL." >&2
    echo "Use --dev to install from the repository default branch." >&2
    exit 1
  fi
  if [[ "$release_channel" == "dev" ]]; then
    echo "Installing NeuroCade from the repository default branch."
    git clone "$REPO_URL" "$install_dir"
  else
    echo "Installing NeuroCade $release_ref from the $release_channel release channel."
    git clone --branch "$release_ref" --depth 1 "$REPO_URL" "$install_dir"
  fi
  if (( ${#forwarded_args[@]} > 0 )); then
    NEUROCADE_INSTALL_CHANNEL="$release_channel" exec bash "$install_dir/scripts/install.sh" "${forwarded_args[@]}"
  fi
  NEUROCADE_INSTALL_CHANNEL="$release_channel" exec bash "$install_dir/scripts/install.sh"
}

bootstrap_from_raw_script "$@"

source "$INSTALL_LIB_DIR/common.sh"
source "$INSTALL_LIB_DIR/python.sh"
source "$INSTALL_LIB_DIR/node.sh"
source "$INSTALL_LIB_DIR/lima.sh"
source "$INSTALL_LIB_DIR/apptainer.sh"
source "$INSTALL_LIB_DIR/env.sh"
source "$INSTALL_LIB_DIR/doctor.sh"

MODE=""
LLM_PROVIDER=""
INSTALL_CHANNEL="$DEFAULT_RELEASE_CHANNEL"
START_STACK=1
START_SKIP_REASON=""
INSTALL_PREREQS=1
INSTALL_FREESURFER=0
DESKTOP_MODE=auto
ASSUME_YES=0
DRY_RUN=0
DOCTOR=0
IMAGE_PREFETCH_PID=""
IMAGE_PREFETCH_LOG=""
SAMPLE_CASE_PREFETCH_PID=""
SAMPLE_CASE_PREFETCH_LOG=""

if [[ "${NEUROCADE_INSTALL_FREESURFER:-}" == "1" ]]; then
  INSTALL_FREESURFER=1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --release-channel)
      if [[ $# -lt 2 ]]; then
        echo "--release-channel requires one of: stable, prerelease, dev" >&2
        exit 2
      fi
      INSTALL_CHANNEL="$(normalize_release_channel "${2:-}")"
      shift 2
      ;;
    --stable)
      INSTALL_CHANNEL="stable"
      shift
      ;;
    --prerelease)
      INSTALL_CHANNEL="prerelease"
      shift
      ;;
    --dev)
      INSTALL_CHANNEL="dev"
      shift
      ;;
    --llm-provider)
      LLM_PROVIDER="${2:-}"
      shift 2
      ;;
    --no-start)
      START_STACK=0
      START_SKIP_REASON="--no-start was provided"
      shift
      ;;
    --no-prereqs)
      INSTALL_PREREQS=0
      shift
      ;;
    --desktop)
      DESKTOP_MODE=enabled
      shift
      ;;
    --no-desktop)
      DESKTOP_MODE=disabled
      shift
      ;;
    --with-freesurfer)
      INSTALL_FREESURFER=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      START_STACK=0
      START_SKIP_REASON="--dry-run was provided"
      shift
      ;;
    --doctor)
      DOCTOR=1
      DRY_RUN=1
      START_STACK=0
      START_SKIP_REASON="--doctor was provided"
      shift
      ;;
    --yes|-y)
      ASSUME_YES=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

start_stack() {
  local root="$1"
  if [[ "$START_STACK" -ne 1 ]]; then
    echo "Configuration written. Skipping stack startup because ${START_SKIP_REASON:-stack startup is disabled}."
    return
  fi
  run_step "Starting NeuroCade stack" bash -lc "cd \"$root\" && ./scripts/apptainer/up.sh -d"
}

start_image_prefetch() {
  local root="$1"
  local container_root tool_catalog container_inventory installed_tools
  container_root="$(env_file_value "$root" NEUROCADE_CONTAINER_ROOT)"
  tool_catalog="$(env_file_value "$root" TOOL_CATALOG_DIR)"
  container_inventory="$(env_file_value "$root" NEUROCADE_CONTAINER_INVENTORY)"
  installed_tools="$(env_file_value "$root" NEUROCADE_INSTALLED_TOOLS_JSONL)"
  container_root="${container_root:-$root/.apptainer/containers}"
  tool_catalog="${tool_catalog:-$root/llm-data/tool-catalog}"
  container_inventory="${container_inventory:-$tool_catalog/installed_containers.json}"
  installed_tools="${installed_tools:-$tool_catalog/installed_tools.jsonl}"

  mkdir -p "$root/.runtime/logs"
  local prefetch_command=("$root/scripts/containers.sh" prefetch core)
  if [[ "$INSTALL_FREESURFER" -eq 1 ]] && freesurfer_license_available "$root"; then
    prefetch_command+=(--with-freesurfer)
  fi
  IMAGE_PREFETCH_LOG="$root/.runtime/logs/image-prefetch.log"
  echo "Starting runtime image prefetch in the background. Log: $IMAGE_PREFETCH_LOG"
  (
    cd "$root"
    export NEUROCADE_CONTAINER_ROOT="$container_root"
    export TOOL_CATALOG_DIR="$tool_catalog"
    export NEUROCADE_CONTAINER_INVENTORY="$container_inventory"
    export NEUROCADE_INSTALLED_TOOLS_JSONL="$installed_tools"
    "${prefetch_command[@]}"
  ) >"$IMAGE_PREFETCH_LOG" 2>&1 &
  IMAGE_PREFETCH_PID="$!"
}

wait_image_prefetch() {
  [[ -n "$IMAGE_PREFETCH_PID" ]] || return 0
  local started_at="$SECONDS"
  local spinner='|/-\'
  local spinner_index=0
  echo "Waiting for runtime image prefetch to finish. Log: $IMAGE_PREFETCH_LOG"
  while kill -0 "$IMAGE_PREFETCH_PID" 2>/dev/null; do
    local elapsed=$((SECONDS - started_at))
    if is_tty; then
      printf '\rRuntime image prefetch still running %s (%ss). Log: %s' "${spinner:$((spinner_index % ${#spinner})):1}" "$elapsed" "$IMAGE_PREFETCH_LOG"
      spinner_index=$((spinner_index + 1))
    elif (( elapsed % 15 == 0 )); then
      echo "Runtime image prefetch still running (${elapsed}s). Log: $IMAGE_PREFETCH_LOG"
    fi
    sleep 1
  done
  if is_tty; then
    printf '\n'
  fi
  if wait "$IMAGE_PREFETCH_PID"; then
    echo "Runtime image prefetch complete."
  else
    echo "Runtime image prefetch did not complete; the normal install step will fetch or fall back as needed."
    echo "Prefetch log: $IMAGE_PREFETCH_LOG"
  fi
  IMAGE_PREFETCH_PID=""
}

cleanup_image_prefetch() {
  [[ -n "$IMAGE_PREFETCH_PID" ]] || return 0
  kill "$IMAGE_PREFETCH_PID" 2>/dev/null || true
  wait "$IMAGE_PREFETCH_PID" 2>/dev/null || true
  IMAGE_PREFETCH_PID=""
}

start_sample_case_prefetch() {
  local root="$1"
  SAMPLE_CASE_PREFETCH_LOG="$root/.runtime/logs/sample-case-prefetch.log"
  mkdir -p "$root/.runtime/logs"
  echo "Starting demo/sample case download in the background. Log: $SAMPLE_CASE_PREFETCH_LOG"
  (
    cd "$root"
    download_sample_case_release_asset "$root"
  ) >"$SAMPLE_CASE_PREFETCH_LOG" 2>&1 &
  SAMPLE_CASE_PREFETCH_PID="$!"
}

wait_sample_case_prefetch() {
  [[ -n "$SAMPLE_CASE_PREFETCH_PID" ]] || return 0
  local started_at="$SECONDS"
  local spinner='|/-\'
  local spinner_index=0
  echo "Waiting for demo/sample case download to finish. Log: $SAMPLE_CASE_PREFETCH_LOG"
  while kill -0 "$SAMPLE_CASE_PREFETCH_PID" 2>/dev/null; do
    local elapsed=$((SECONDS - started_at))
    if is_tty; then
      printf '\rDemo/sample case download still running %s (%ss). Log: %s' "${spinner:$((spinner_index % ${#spinner})):1}" "$elapsed" "$SAMPLE_CASE_PREFETCH_LOG"
      spinner_index=$((spinner_index + 1))
    elif (( elapsed % 15 == 0 )); then
      echo "Demo/sample case download still running (${elapsed}s). Log: $SAMPLE_CASE_PREFETCH_LOG"
    fi
    sleep 1
  done
  if is_tty; then
    printf '\n'
  fi
  if wait "$SAMPLE_CASE_PREFETCH_PID"; then
    echo "Demo/sample case download complete."
  else
    echo "Warning: demo/sample case artifact could not be found or downloaded; skipping sample case. Log: $SAMPLE_CASE_PREFETCH_LOG" >&2
  fi
  SAMPLE_CASE_PREFETCH_PID=""
}

cleanup_sample_case_prefetch() {
  [[ -n "$SAMPLE_CASE_PREFETCH_PID" ]] || return 0
  kill "$SAMPLE_CASE_PREFETCH_PID" 2>/dev/null || true
  wait "$SAMPLE_CASE_PREFETCH_PID" 2>/dev/null || true
  SAMPLE_CASE_PREFETCH_PID=""
}

cleanup_prefetches() {
  cleanup_image_prefetch
  cleanup_sample_case_prefetch
}
trap cleanup_prefetches EXIT

should_setup_desktop() {
  local mode="$1"
  case "$DESKTOP_MODE" in
    enabled)
      return 0
      ;;
    disabled)
      return 1
      ;;
    auto)
      [[ "$mode" == "local" ]]
      return
      ;;
  esac
  return 1
}

setup_desktop_launcher() {
  local root="$1"
  ensure_desktop_prerequisites "$root"
  if ! command -v npm >/dev/null 2>&1; then
    echo "Skipping desktop setup because npm is not available."
    echo "Install Node.js/npm and run: ./scripts/desktop/run.sh"
    return
  fi
  if client_dependencies_current "$root"; then
    echo "Electron desktop dependencies already installed."
  else
    log_section "Installing Electron desktop dependencies"
    (cd "$root/client" && npm ci)
  fi
  "$root/scripts/desktop/install_launcher.sh"
}

sample_case_prepared() {
  local root="$1"
  local sample_dir="$root/sample_case/FastSurfer_Rhineland_0000"
  [[ -f "$sample_dir/mri/orig.mgz" ]] && \
  [[ -f "$sample_dir/mri/aparc.DKTatlas+aseg.deep.mgz" ]] && \
  [[ -f "$sample_dir/surf/lh.pial" ]] && \
  [[ -f "$sample_dir/logs/stderr.log" ]]
}

verify_release_asset_checksum() {
  local archive="$1"
  local filename="$2"
  local sums_url="$3"
  local tmp_dir sums_file expected actual
  tmp_dir="$(mktemp -d)"
  sums_file="$tmp_dir/SHA256SUMS.txt"
  if ! curl -fsSL "$sums_url" -o "$sums_file"; then
    rm -rf "$tmp_dir"
    echo "Checksum file unavailable; continuing without checksum verification."
    return 0
  fi
  expected="$(awk -v name="$filename" '$2 == name { print $1; exit }' "$sums_file")"
  if [[ -z "$expected" ]]; then
    rm -rf "$tmp_dir"
    echo "No checksum entry found for $filename; continuing without checksum verification."
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$archive" | awk '{print $1}')"
  else
    rm -rf "$tmp_dir"
    echo "No SHA-256 tool found; continuing without checksum verification."
    return 0
  fi
  rm -rf "$tmp_dir"
  if [[ "$actual" != "$expected" ]]; then
    echo "Checksum verification failed for $filename" >&2
    return 1
  fi
}

sample_case_candidate_release_tags() {
  local current="${NEUROCADE_CONTAINER_RELEASE_TAG:-}"
  {
    case "${INSTALL_CHANNEL:-stable}" in
      prerelease)
        prerelease_tags_desc
        ;;
      dev)
        prerelease_tags_desc
        stable_release_tags_desc
        ;;
      stable|*)
        stable_release_tags_desc
        ;;
    esac
  } | awk -v current="$current" '$0 != current && !seen[$0]++'
}

sample_case_release_asset_url_for_resolved_tag() {
  local tag="$1"
  local filename="$2"
  if [[ -z "$tag" || "$tag" == "latest" ]]; then
    neurocade_release_asset_url "$filename"
  else
    neurocade_release_asset_url_for_tag "$tag" "$filename"
  fi
}

resolve_sample_case_release_asset_tag() {
  local default_tag="${NEUROCADE_CONTAINER_RELEASE_TAG:-latest}"
  local default_url tag url
  default_url="$(sample_case_release_asset_url_for_resolved_tag "$default_tag" "$SAMPLE_CASE_ARTIFACT_NAME")"
  if release_asset_url_available "$default_url"; then
    printf '%s\n' "$default_tag"
    return 0
  fi

  echo "Demo/sample case asset is unavailable at $default_url; scanning older release assets." >&2
  while IFS= read -r tag; do
    [[ -n "$tag" ]] || continue
    url="$(neurocade_release_asset_url_for_tag "$tag" "$SAMPLE_CASE_ARTIFACT_NAME")"
    if release_asset_url_available "$url"; then
      echo "Using demo/sample case asset from $tag." >&2
      printf '%s\n' "$tag"
      return 0
    fi
  done < <(sample_case_candidate_release_tags || true)

  return 1
}

download_sample_case_release_asset() {
  local root="$1"
  sample_case_prepared "$root" && return 0
  local sample_release_tag url sums_url tmp_dir archive
  sample_release_tag="$(resolve_sample_case_release_asset_tag)" || return 1
  url="$(sample_case_release_asset_url_for_resolved_tag "$sample_release_tag" "$SAMPLE_CASE_ARTIFACT_NAME")"
  sums_url="$(sample_case_release_asset_url_for_resolved_tag "$sample_release_tag" "SHA256SUMS.txt")"
  tmp_dir="$(mktemp -d)"
  archive="$tmp_dir/$SAMPLE_CASE_ARTIFACT_NAME"
  echo "Downloading demo/sample case from $url"
  if ! curl -fsSL "$url" -o "$archive"; then
    rm -rf "$tmp_dir"
    return 1
  fi
  if ! verify_release_asset_checksum "$archive" "$SAMPLE_CASE_ARTIFACT_NAME" "$sums_url"; then
    rm -rf "$tmp_dir"
    return 1
  fi
  if ! mkdir -p "$root/sample_case"; then
    rm -rf "$tmp_dir"
    return 1
  fi
  if ! tar -C "$root/sample_case" -xzf "$archive"; then
    rm -rf "$tmp_dir"
    return 1
  fi
  rm -rf "$tmp_dir"
  if ! sample_case_prepared "$root"; then
    echo "Downloaded sample case archive did not contain the expected files." >&2
    return 1
  fi
  echo "Demo/sample case installed at $root/sample_case/FastSurfer_Rhineland_0000."
}

print_completion() {
  local root="$1"
  local app_url
  app_url="$(env_file_value "$root" APP_BASE_URL)"
  cat <<EOF

NeuroCade setup complete.
Application URL: $app_url
Configuration: $root/.env
Install log: $root/.runtime/logs/install.log

Useful commands:
  cd "$root"
  ./scripts/desktop/run.sh
  ./scripts/apptainer/up.sh -d
  ./scripts/apptainer/status.sh
EOF
}

main() {
  require_supported_os
  ensure_prerequisites
  local root
  root="$(ensure_checkout)"
  cd "$root"

  if [[ -z "$MODE" ]]; then
    MODE="$(env_file_value "$root" DEPLOYMENT_PROFILE)"
    if [[ -z "$MODE" ]]; then
      MODE="$(
        choose "Deployment profile" "local" \
          "local|Single user on this machine; localhost only, local auth enabled." \
          "internal|Authenticated institutional server for de-identified research MRI." \
          "demo|Public sample-data instance with restricted actions."
      )"
    fi
  fi
  MODE="$(normalize_mode "$MODE")"
  if [[ "$DESKTOP_MODE" == "enabled" && "$MODE" != "local" ]]; then
    echo "The Electron desktop launcher is supported only for --mode local." >&2
    exit 2
  fi
  if [[ -z "$LLM_PROVIDER" ]]; then
    LLM_PROVIDER="$(env_file_value "$root" LLM_PROVIDER_DEFAULT)"
    if [[ -z "$LLM_PROVIDER" ]]; then
      LLM_PROVIDER="$(
        choose "LLM provider" "openai-compatible" \
          "openai-compatible|Custom OpenAI-compatible API base URL." \
          "anthropic|Anthropic Claude API." \
          "google|Google Gemini API." \
          "ollama|Local Ollama server."
      )"
    fi
  fi
  LLM_PROVIDER="$(normalize_provider "$LLM_PROVIDER")"
  INSTALL_CHANNEL="$(normalize_release_channel "${NEUROCADE_INSTALL_CHANNEL:-$INSTALL_CHANNEL}")"
  export NEUROCADE_INSTALL_CHANNEL="$INSTALL_CHANNEL"
  configure_container_release_tag "$root"

  if [[ "$DOCTOR" -eq 1 ]]; then
    run_doctor "$root" "$MODE" "$LLM_PROVIDER"
    return
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run_doctor "$root" "$MODE" "$LLM_PROVIDER"
    echo
    echo "Dry run only. No files were written and no services were started."
    return
  fi

  setup_install_logging "$root"
  run_step "Preparing Python runtime" ensure_python_runtime "$root"
  start_image_prefetch "$root"
  start_sample_case_prefetch "$root"
  run_step "Preparing local Node.js runtime" ensure_node "$root"
  run_step "Preparing Apptainer runtime" ensure_apptainer "$root"
  run_step "Writing configuration" write_env "$root" "$MODE" "$LLM_PROVIDER"
  run_step "Installing infrastructure images" "$root/scripts/apptainer/images.sh" infra
  wait_image_prefetch
  local install_command=("$root/scripts/containers.sh" install core --source auto)
  if [[ "$INSTALL_FREESURFER" -eq 1 ]]; then
    install_command+=(--with-freesurfer)
  fi
  run_step "Installing core runtime containers" "${install_command[@]}"
  wait_sample_case_prefetch
  if should_setup_desktop "$MODE"; then
    setup_desktop_launcher "$root"
    START_STACK=0
    START_SKIP_REASON="the Electron desktop launcher will start the local backend on first launch"
  fi

  start_stack "$root"
  print_completion "$root"
}

main "$@"
