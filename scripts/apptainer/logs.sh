#!/usr/bin/env bash
# Purpose:
#   Manages the Apptainer logs workflow for NeuroCade.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

follow=0
offsets_file=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -f|--follow)
      follow=1
      shift
      ;;
    --from-offsets)
      offsets_file="${2:-}"
      if [[ -z "$offsets_file" ]]; then
        echo "--from-offsets requires a file path." >&2
        exit 2
      fi
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unsupported argument for scripts/apptainer/logs.sh: $1" >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

service_offset() {
  local service="$1"
  [[ -n "$offsets_file" && -f "$offsets_file" ]] || {
    printf '0\n'
    return 0
  }
  awk -v service="$service" '$1 == service { print $2; found = 1 } END { if (!found) print 0 }' "$offsets_file"
}

if [[ "$#" -gt 0 ]]; then
  services=("$@")
else
  services=("${RUNTIME_LOG_SERVICES[@]}")
fi

files=()
for service in "${services[@]}"; do
  file="$(service_log_file "$service")"
  [[ -f "$file" ]] || continue
  files+=("$file")
done

if [[ "${#files[@]}" -eq 0 ]]; then
  echo "No runtime log files found." >&2
  exit 0
fi

if [[ -n "$offsets_file" ]]; then
  for service in "${services[@]}"; do
    file="$(service_log_file "$service")"
    [[ -f "$file" ]] || continue
    offset="$(service_offset "$service")"
    size="$(file_size_bytes "$file")"
    [[ "$offset" =~ ^[0-9]+$ ]] || offset=0
    if (( size > offset )); then
      if [[ "${#files[@]}" -gt 1 ]]; then
        printf '\n==> %s <==\n' "$file"
      fi
      tail -c +"$((offset + 1))" "$file"
    fi
  done
fi

if [[ "$follow" -eq 1 ]]; then
  if [[ -n "$offsets_file" ]]; then
    tail -n 0 -f "${files[@]}"
  else
    tail -n 100 -f "${files[@]}"
  fi
elif [[ -z "$offsets_file" ]]; then
  tail -n 100 "${files[@]}"
else
  :
fi
