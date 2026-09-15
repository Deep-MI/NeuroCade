#!/usr/bin/env bash
# Transactional updater for installer-owned NeuroCade archive installations.
set -euo pipefail

UPDATER_VERSION=1
ROOT_DIR="${NEUROCADE_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY="${NEUROCADE_RELEASE_REPOSITORY:-Deep-MI/NeuroCade}"
CHANNEL=stable
SELECTOR=""
CHECK_ONLY=0
ASSUME_YES=0
STAGED_SOURCE=""
STAGED_MANIFEST=""
KEEP_WORK_DIR=0
TRANSACTION_DIR=""

usage() {
  cat <<'EOF'
Usage: ./scripts/update.sh [--check] [--channel stable|beta] [--version TAG] [--yes]

Downloads a verified NeuroCade release, preserves configuration and data, and
automatically restores the previous source, database, and runtime artifact if
the updated application does not start successfully.
EOF
}

fail() { echo "ERROR: $*" >&2; exit 1; }
download() {
  local url="$1" output="$2"
  curl --fail --location --silent --show-error --retry 4 --retry-all-errors --connect-timeout 15 -o "$output" "$url"
}
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}
verify_asset() {
  local asset="$1" checksum="$2" expected
  expected="$(awk 'NR == 1 {print $1}' "$checksum")"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || fail "Invalid checksum file for $(basename "$asset")"
  expected="$(printf '%s' "$expected" | tr '[:upper:]' '[:lower:]')"
  [[ "$(sha256 "$asset")" == "$expected" ]] || fail "Checksum verification failed for $(basename "$asset")"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    --channel) [[ "${2:-}" == stable || "${2:-}" == beta ]] || fail "--channel must be stable or beta"; CHANNEL="$2"; shift 2 ;;
    --version) [[ "${2:-}" =~ ^v[0-9][A-Za-z0-9._-]*$ ]] || fail "--version requires a v-prefixed release tag"; SELECTOR="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --bootstrap-staged) STAGED_SOURCE="$2"; STAGED_MANIFEST="$3"; shift 3 ;;
    --) fail "Installer options cannot be forwarded through an update; runtime changes and --no-start are not supported" ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "Python 3 is required"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/neurocade-update.XXXXXX")"
cleanup() {
  (( KEEP_WORK_DIR )) || rm -rf "$WORK_DIR"
  if (( ! KEEP_WORK_DIR )) && [[ -n "$TRANSACTION_DIR" ]]; then rm -rf "$TRANSACTION_DIR"; fi
}
trap cleanup EXIT
MANIFEST="$WORK_DIR/neurocade-release.json"

if [[ -n "$STAGED_SOURCE" ]]; then
  cp "$STAGED_MANIFEST" "$MANIFEST"
else
  if [[ -z "$SELECTOR" && "$CHANNEL" == stable ]]; then
    manifest_url="${NEUROCADE_RELEASE_MANIFEST_URL:-https://github.com/$REPOSITORY/releases/latest/download/neurocade-release.json}"
    download "$manifest_url" "$MANIFEST"
  else
    if [[ -z "$SELECTOR" ]]; then
      releases="$WORK_DIR/releases.json"
      download "${NEUROCADE_RELEASES_API_URL:-https://api.github.com/repos/$REPOSITORY/releases?per_page=100}" "$releases"
      SELECTOR="$(python3 "$TOOL_ROOT/scripts/release/release_manifest.py" resolve "$releases" --channel "$CHANNEL" | sed -n '1p')"
    fi
    download "https://github.com/$REPOSITORY/releases/download/$SELECTOR/neurocade-release.json" "$MANIFEST"
  fi
fi

mapfile_cmd=()
while IFS= read -r value; do mapfile_cmd+=("$value"); done < <(python3 "$TOOL_ROOT/scripts/release/release_manifest.py" read-update "$MANIFEST")
(( ${#mapfile_cmd[@]} == 6 )) || fail "Incomplete updater metadata"
TAG="${mapfile_cmd[0]}"; VERSION="${mapfile_cmd[1]}"; REVISION="${mapfile_cmd[2]}"
MINIMUM="${mapfile_cmd[3]}"; SOURCE_NAME="${mapfile_cmd[4]}"; CHECKSUM_NAME="${mapfile_cmd[5]}"
(( MINIMUM <= UPDATER_VERSION )) || fail "Release $TAG requires a newer updater"
[[ -z "$SELECTOR" || "$TAG" == "$SELECTOR" ]] || fail "Release manifest tag does not match requested version $SELECTOR"

current=""
[[ -s "$ROOT_DIR/.runtime/provenance.json" ]] && current="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version", ""))' "$ROOT_DIR/.runtime/provenance.json")"
if [[ "$current" == "$VERSION" ]]; then
  echo "NeuroCade $VERSION is already installed."
  exit 0
fi
if (( CHECK_ONLY )); then
  echo "${current:-unknown} -> $VERSION ($CHANNEL)"
  exit 0
fi

[[ ! -d "$ROOT_DIR/.git" ]] || fail "This is a Git checkout. Update it with Git, then rerun scripts/install.sh."
[[ -f "$ROOT_DIR/.runtime/runtime-root-owned" ]] || fail "Refusing to update a directory not owned by the NeuroCade installer"
LOCK="$ROOT_DIR/.runtime/update.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  lock_pid="$(sed -n '1p' "$LOCK/pid" 2>/dev/null || true)"
  if [[ "$lock_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
    rm -rf "$LOCK"
    mkdir "$LOCK" || fail "Could not replace a stale NeuroCade update lock"
  else
    fail "Another NeuroCade update is already running"
  fi
fi
printf '%s\n' "$$" >"$LOCK/pid"
release_update_lock() {
  rm -f "$LOCK/pid"
  rmdir "$LOCK" 2>/dev/null || true
}
trap 'release_update_lock; cleanup' EXIT

SOURCE_ARCHIVE="$WORK_DIR/$SOURCE_NAME"
SOURCE_CHECKSUM="$WORK_DIR/$CHECKSUM_NAME"
if [[ -n "$STAGED_SOURCE" ]]; then
  cp "$STAGED_SOURCE" "$SOURCE_ARCHIVE"
  staged_checksum="$(dirname "$STAGED_SOURCE")/$CHECKSUM_NAME"
  [[ -f "$staged_checksum" ]] || fail "The staged source checksum is missing"
  cp "$staged_checksum" "$SOURCE_CHECKSUM"
else
  base="https://github.com/$REPOSITORY/releases/download/$TAG"
  download "$base/$SOURCE_NAME" "$SOURCE_ARCHIVE"
  download "$base/$CHECKSUM_NAME" "$SOURCE_CHECKSUM"
fi
verify_asset "$SOURCE_ARCHIVE" "$SOURCE_CHECKSUM"
SOURCE_IDENTITY="$SOURCE_NAME@sha256:$(sha256 "$SOURCE_ARCHIVE")"
EXTRACT_DIR="$WORK_DIR/extracted"; mkdir "$EXTRACT_DIR"
SOURCE_ROOT="$(python3 "$TOOL_ROOT/scripts/update_source.py" extract "$SOURCE_ARCHIVE" "$EXTRACT_DIR")"

if [[ -s "$ROOT_DIR/.runtime/source-manifest.json" ]]; then
  python3 "$TOOL_ROOT/scripts/update_source.py" check "$ROOT_DIR" "$ROOT_DIR/.runtime/source-manifest.json" || fail "Move or revert those edits before updating."
elif (( ! ASSUME_YES )) && [[ -t 0 ]]; then
  read -r -p "This legacy install has no source-change record. Continue with a full rollback backup? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || exit 1
else
  echo "Legacy install: creating a complete managed-source rollback backup."
fi

source "$ROOT_DIR/scripts/lib/env.sh"
ENV_FILE="$ROOT_DIR/.env"; load_env_file

active_workflow_count() {
  local pid_file="$ROOT_DIR/.runtime/bridge.pid" token_file="$ROOT_DIR/.runtime/bridge-token"
  if [[ ! -s "$pid_file" ]] || ! kill -0 "$(sed -n '1p' "$pid_file")" 2>/dev/null; then
    echo 0
    return
  fi
  [[ -s "$token_file" ]] || return 1
  python3 - "$token_file" "${NEUROCADE_BRIDGE_PORT:-8765}" "$ROOT_DIR/.runtime/launch-id" <<'PY'
import json, sys, urllib.request
token, port, launch_file = sys.argv[1:]
headers = {"Authorization": "Bearer " + open(token).read().strip()}
try:
    launch_id = open(launch_file).read().strip()
except FileNotFoundError:
    launch_id = ""
if launch_id:
    headers["X-NeuroCade-Launch-ID"] = launch_id
request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/health", headers=headers)
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
value = payload.get("active_runs")
if not isinstance(value, int) or value < 0:
    raise SystemExit(1)
print(value)
PY
}
active_runs="$(active_workflow_count)" || fail "Could not determine whether workflows are active"
[[ "$active_runs" =~ ^[0-9]+$ ]] || fail "Invalid active workflow count"
(( active_runs == 0 )) || fail "$active_runs workflow(s) are still active; wait for them before updating"

runtime="${NEUROCADE_RUNTIME:-}"; mode="${DEPLOYMENT_PROFILE:-local}"; provider="${LLM_PROVIDER_DEFAULT:-no-llm}"
[[ "$runtime" == docker || "$runtime" == apptainer ]] || fail "The installed runtime is not configured"
TRANSACTION_DIR="$ROOT_DIR/.runtime/update-transaction"
[[ ! -e "$TRANSACTION_DIR" ]] || fail "An interrupted update recovery set already exists at $TRANSACTION_DIR; inspect it before retrying"
mkdir "$TRANSACTION_DIR"
BACKUP="$TRANSACTION_DIR/rollback"; mkdir -p "$BACKUP/source" "$BACKUP/database" "$BACKUP/metadata"
cp "$ROOT_DIR/.env" "$BACKUP/env"
for metadata_name in provenance.json source-manifest.json; do
  if [[ -f "$ROOT_DIR/.runtime/$metadata_name" ]]; then
    cp "$ROOT_DIR/.runtime/$metadata_name" "$BACKUP/metadata/$metadata_name"
    : >"$BACKUP/metadata/$metadata_name.present"
  fi
done
RECORD="$BACKUP/source-record.json"
old_image="${NEUROCADE_IMAGE:-}"
old_image_id=""
if [[ "$runtime" == docker ]]; then
  old_image_id="$(docker image inspect --format '{{.Id}}' "$old_image" 2>/dev/null || true)"
  [[ -n "$old_image" && -n "$old_image_id" ]] || fail "The currently configured Docker image is unavailable; refusing an update without rollback"
else
  cp -a "$ROOT_DIR/.runtime/images" "$BACKUP/images"
  if [[ -e "$ROOT_DIR/.runtime/release" ]]; then
    cp -a "$ROOT_DIR/.runtime/release" "$BACKUP/release" || fail "Could not back up the installed Apptainer release artifacts"
  fi
fi
{
  printf 'runtime=%s\n' "$runtime"
  printf 'from_version=%s\n' "${current:-legacy}"
  printf 'to_version=%s\n' "$VERSION"
  printf 'old_image=%s\n' "$old_image"
  printf 'old_image_id=%s\n' "$old_image_id"
} >"$TRANSACTION_DIR/recovery-info"

PHASE=prepared
rollback() {
  local status=$? restore_failed=0 metadata_name
  trap - ERR INT TERM
  set +e
  if [[ "$PHASE" != mutating ]]; then
    echo "Update interrupted before any installed state changed; restarting the previous application." >&2
    "$ROOT_DIR/scripts/run.sh" start -d >/dev/null 2>&1 || restore_failed=1
  else
    echo "Update failed; restoring NeuroCade ${current:-previous version}." >&2
    if ! "$ROOT_DIR/scripts/run.sh" stop >/dev/null 2>&1; then
      KEEP_WORK_DIR=1
      echo "ROLLBACK INCOMPLETE: the updated application could not be stopped. Recovery backup retained at $BACKUP" >&2
      (( status != 0 )) || status=1
      exit "$status"
    fi
    python3 "$SOURCE_ROOT/scripts/update_source.py" rollback "$ROOT_DIR" "$BACKUP/source" "$RECORD" || restore_failed=1
    cp "$BACKUP/env" "$ROOT_DIR/.env" || restore_failed=1
    if [[ "$runtime" == docker ]]; then
      docker tag "$old_image_id" "$old_image" >/dev/null || restore_failed=1
      docker run --rm -v "${NEUROCADE_DATABASE_VOLUME:-neurocade-database}:/database" -v "$BACKUP/database:/backup:ro" --entrypoint sh "$old_image" -c 'rm -rf /database/* /database/.[!.]* /database/..?*; cp -a /backup/. /database/' || restore_failed=1
    else
      rm -rf "$ROOT_DIR/.runtime/database.restore" "$ROOT_DIR/.runtime/images.restore" "$ROOT_DIR/.runtime/release.restore"
      cp -a "$BACKUP/database" "$ROOT_DIR/.runtime/database.restore" || restore_failed=1
      cp -a "$BACKUP/images" "$ROOT_DIR/.runtime/images.restore" || restore_failed=1
      if [[ -d "$BACKUP/release" ]]; then cp -a "$BACKUP/release" "$ROOT_DIR/.runtime/release.restore" || restore_failed=1; fi
      if (( restore_failed == 0 )); then
        rm -rf "$ROOT_DIR/.runtime/database" "$ROOT_DIR/.runtime/images" "$ROOT_DIR/.runtime/release"
        mv "$ROOT_DIR/.runtime/database.restore" "$ROOT_DIR/.runtime/database" || restore_failed=1
        mv "$ROOT_DIR/.runtime/images.restore" "$ROOT_DIR/.runtime/images" || restore_failed=1
        if [[ -d "$ROOT_DIR/.runtime/release.restore" ]]; then mv "$ROOT_DIR/.runtime/release.restore" "$ROOT_DIR/.runtime/release" || restore_failed=1; fi
      fi
    fi
    for metadata_name in provenance.json source-manifest.json; do
      if [[ -f "$BACKUP/metadata/$metadata_name.present" ]]; then
        cp "$BACKUP/metadata/$metadata_name" "$ROOT_DIR/.runtime/$metadata_name" || restore_failed=1
      else
        rm -f "$ROOT_DIR/.runtime/$metadata_name" || restore_failed=1
      fi
    done
    if (( restore_failed == 0 )); then
      "$ROOT_DIR/scripts/run.sh" start -d >/dev/null 2>&1 || restore_failed=1
    fi
  fi
  if (( restore_failed )); then
    KEEP_WORK_DIR=1
    echo "ROLLBACK INCOMPLETE: the previous application was not restarted. Recovery backup retained at $BACKUP" >&2
  fi
  (( status != 0 )) || status=1
  exit "$status"
}

trap rollback ERR INT TERM
PHASE=stopped
"$ROOT_DIR/scripts/run.sh" stop
if [[ "$runtime" == docker ]]; then
  if ! docker run --rm -v "${NEUROCADE_DATABASE_VOLUME:-neurocade-database}:/database:ro" -v "$BACKUP/database:/backup" --entrypoint sh "$old_image" -c 'cp -a /database/. /backup/'; then
    "$ROOT_DIR/scripts/run.sh" start -d >/dev/null 2>&1 || true
    fail "Database backup failed; the previous application was restarted"
  fi
else
  if ! cp -a "$ROOT_DIR/.runtime/database/." "$BACKUP/database/" 2>/dev/null; then
    "$ROOT_DIR/scripts/run.sh" start -d >/dev/null 2>&1 || true
    fail "Database backup failed; the previous application was restarted"
  fi
fi
PHASE=mutating
python3 "$TOOL_ROOT/scripts/update_source.py" apply "$SOURCE_ROOT" "$ROOT_DIR" "$BACKUP/source" "$RECORD"
args=(--runtime "$runtime" --mode "$mode" --llm-provider "$provider" --yes)
if [[ "$runtime" == apptainer ]]; then args+=(--version "$TAG"); fi
NEUROCADE_INSTALL_VERSION="$VERSION" NEUROCADE_INSTALL_REVISION="$REVISION" \
NEUROCADE_INSTALL_CHANNEL="$CHANNEL" NEUROCADE_INSTALL_ARTIFACT="$SOURCE_IDENTITY" \
  "$ROOT_DIR/scripts/install.sh" "${args[@]}"
trap - ERR INT TERM
echo "Updated NeuroCade ${current:-legacy} -> $VERSION."
