#!/usr/bin/env bash
# Checks before downloading toolchains, images or modifying configuration.
install_preflight() {
  local root="$1" runtime="$2" data available minimum="${NEUROCADE_MIN_FREE_KB:-8388608}"
  case "$(uname -s):$(neurocade_host_arch)" in
    Darwin:arm64|Darwin:x86_64|Linux:x86_64|Linux:amd64) ;;
    *) echo "Unsupported host: $(uname -s) $(neurocade_host_arch). Use macOS with Docker or Linux amd64 with Docker/rootless Apptainer." >&2; return 1 ;;
  esac
  if [[ "$runtime" == docker || "${BUILD_FROM_SOURCE:-0}" == 1 ]]; then
    if ! docker info >/dev/null 2>&1; then
      echo "Docker is installed, but its engine is not responding or this user cannot access it." >&2
      echo "Start Docker Desktop (macOS) or ask your administrator to enable Docker access (Linux), then rerun this command." >&2
      return 1
    fi
  fi
  [[ -w "$root" ]] || { echo "Install directory is not writable: $root. Choose a directory owned by your user." >&2; return 1; }
  data="$(configured_or_default "$root" HOST_DATA_DIR "$root/neurocade-data")"
  [[ "$data" == /* ]] || data="$root/$data"
  while [[ ! -e "$data" ]]; do data="$(dirname "$data")"; done
  [[ -d "$data" && -w "$data" ]] || { echo "Data location is not writable: $data. Set HOST_DATA_DIR to a writable directory." >&2; return 1; }
  [[ "$minimum" =~ ^[0-9]+$ ]] || { echo "NEUROCADE_MIN_FREE_KB must be a non-negative integer." >&2; return 2; }
  available="$(df -Pk "$data" | awk 'NR==2 {print $4}')"
  [[ "$available" =~ ^[0-9]+$ ]] && (( available >= minimum )) || {
    echo "Insufficient free space at $data: need at least $(( (minimum+1048575)/1048576 )) GiB for setup. Free space or choose another HOST_DATA_DIR. Workflow images may need more." >&2; return 1;
  }
  echo "Host checks passed: $runtime, writable storage, sufficient setup space."
}
