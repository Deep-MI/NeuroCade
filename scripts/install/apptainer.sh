#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer apptainer workflow.


install_apptainer_linux() {
  local root="$1"
  local install_dir="${APPTAINER_INSTALL_DIR:-$root/$LOCAL_APPTAINER_DIR_REL}"
  local missing=()
  for cmd in bash curl cpio rpm2cpio; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    cat >&2 <<EOF
Apptainer is missing, and the unprivileged Linux installer needs: ${missing[*]}.

Install those host tools with your site-approved package manager, then rerun:
  ./scripts/install.sh --mode ${MODE:-local}
EOF
    exit 1
  fi

  echo "Installing Apptainer locally to $install_dir ..."
  mkdir -p "$install_dir"
  curl -fsSL https://raw.githubusercontent.com/apptainer/apptainer/main/tools/install-unprivileged.sh | bash -s - "$install_dir"
  if [[ ! -x "$install_dir/bin/apptainer" ]]; then
    echo "Apptainer installer completed, but $install_dir/bin/apptainer was not created." >&2
    exit 1
  fi
  export APPTAINER_BIN="$install_dir/bin/apptainer"
  echo "Apptainer installed: $APPTAINER_BIN"
}

host_memory_gib_macos() {
  local bytes
  bytes="$(sysctl -n hw.memsize 2>/dev/null || true)"
  if [[ "$bytes" =~ ^[0-9]+$ && "$bytes" -gt 0 ]]; then
    awk -v bytes="$bytes" 'BEGIN { printf "%.0f\n", bytes / 1024 / 1024 / 1024 }'
  else
    printf '0\n'
  fi
}

host_cpu_count_macos() {
  local cpus
  cpus="$(sysctl -n hw.ncpu 2>/dev/null || true)"
  if [[ "$cpus" =~ ^[0-9]+$ && "$cpus" -gt 0 ]]; then
    printf '%s\n' "$cpus"
  else
    printf '0\n'
  fi
}

host_disk_free_gib() {
  local path="$1"
  df -g "$path" 2>/dev/null | awk 'NR == 2 { print $4 + 0; found = 1 } END { if (!found) print 0 }'
}

integer_or_default() {
  local value="$1"
  local default_value="$2"
  if [[ "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default_value"
  fi
}

default_lima_memory_gib() {
  local host_memory_gib="$1"
  local recommended=12
  local host_reserve=4
  local max_sensible
  if [[ "$host_memory_gib" =~ ^[0-9]+$ && "$host_memory_gib" -gt 0 ]]; then
    max_sensible=$((host_memory_gib - host_reserve))
    if (( max_sensible >= recommended )); then
      printf '%s\n' "$recommended"
    elif (( max_sensible >= 4 )); then
      printf '%s\n' "$max_sensible"
    elif (( host_memory_gib > 1 )); then
      printf '%s\n' "$((host_memory_gib - 1))"
    else
      printf '1\n'
    fi
  else
    printf '%s\n' "$recommended"
  fi
}

default_lima_cpus() {
  local host_cpus="$1"
  local recommended=4
  if [[ "$host_cpus" =~ ^[0-9]+$ && "$host_cpus" -gt 0 ]]; then
    if (( host_cpus >= recommended + 2 )); then
      printf '%s\n' "$recommended"
    elif (( host_cpus > 2 )); then
      printf '%s\n' "$((host_cpus - 1))"
    else
      printf '%s\n' "$host_cpus"
    fi
  else
    printf '%s\n' "$recommended"
  fi
}

default_lima_disk_gib() {
  local free_disk_gib="$1"
  local recommended=100
  local host_reserve=20
  local max_sensible
  if [[ "$free_disk_gib" =~ ^[0-9]+$ && "$free_disk_gib" -gt 0 ]]; then
    max_sensible=$((free_disk_gib - host_reserve))
    if (( max_sensible >= recommended )); then
      printf '%s\n' "$recommended"
    elif (( max_sensible >= 40 )); then
      printf '%s\n' "$max_sensible"
    elif (( max_sensible > 0 )); then
      printf '%s\n' "$max_sensible"
    else
      printf '1\n'
    fi
  else
    printf '%s\n' "$recommended"
  fi
}

resource_display_value() {
  local value="$1"
  if [[ "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]]; then
    printf '%s\n' "$value"
  else
    printf 'unknown\n'
  fi
}

prompt_lima_vm_size_macos() {
  local root="$1"
  local host_memory_gib host_cpus free_disk_gib
  local recommended_memory=12
  local recommended_cpus=4
  local recommended_disk=100
  local memory_default cpus_default disk_default

  host_memory_gib="$(host_memory_gib_macos)"
  host_cpus="$(host_cpu_count_macos)"
  free_disk_gib="$(host_disk_free_gib "$root")"

  memory_default="${LIMA_MEMORY_GIB:-$(default_lima_memory_gib "$host_memory_gib")}"
  cpus_default="${LIMA_CPUS:-$(default_lima_cpus "$host_cpus")}"
  disk_default="${LIMA_DISK_GIB:-$(default_lima_disk_gib "$free_disk_gib")}"

  cat <<EOF

Lima VM sizing for local Apptainer
Detected host resources:
  Memory: $(resource_display_value "$host_memory_gib") GiB total
  CPU: $(resource_display_value "$host_cpus") logical cores
  Disk free near checkout: $(resource_display_value "$free_disk_gib") GiB

Recommended for CPU FastSurfer: at least ${recommended_memory} GiB RAM, ${recommended_cpus} CPUs, and ${recommended_disk} GiB disk.
To keep macOS responsive, leave roughly 4 GiB RAM, 1-2 CPU cores, and 20 GiB disk free for the host.
EOF

  LIMA_MEMORY_GIB="$(integer_or_default "$(prompt "Lima VM memory in GiB" "$memory_default")" "$memory_default")"
  LIMA_CPUS="$(integer_or_default "$(prompt "Lima VM CPU cores" "$cpus_default")" "$cpus_default")"
  LIMA_DISK_GIB="$(integer_or_default "$(prompt "Lima VM disk in GiB" "$disk_default")" "$disk_default")"

  if [[ "$LIMA_MEMORY_GIB" -lt "$recommended_memory" ]]; then
    echo "Warning: ${LIMA_MEMORY_GIB} GiB RAM is below the recommended ${recommended_memory} GiB for CPU FastSurfer and may cause segmentation runs to fail."
  fi
  if [[ "$LIMA_CPUS" -lt "$recommended_cpus" ]]; then
    echo "Warning: ${LIMA_CPUS} CPU cores is below the recommended ${recommended_cpus} cores for CPU FastSurfer; processing will be slower."
  fi
  if [[ "$LIMA_DISK_GIB" -lt "$recommended_disk" ]]; then
    echo "Warning: ${LIMA_DISK_GIB} GiB disk is below the recommended ${recommended_disk} GiB for local images, cache, and MRI outputs."
  fi

  export LIMA_MEMORY_GIB LIMA_CPUS LIMA_DISK_GIB
}

install_apptainer_macos() {
  local root="$1"
  local wrapper_dir="$root/.apptainer/bin"
  local wrapper="$wrapper_dir/apptainer"
  local timeout="${APPTAINER_VM_START_TIMEOUT:-1800}"
  local fallback_after="${APPTAINER_VM_FALLBACK_AFTER:-300}"
  local release_version="${APPTAINER_RELEASE_VERSION:-1.4.5}"

  cat <<EOF
Apptainer needs a Linux kernel and cannot run natively on macOS.
NeuroCade uses Lima to provide that Linux runtime on macOS.
First-time Lima setup downloads an Ubuntu image, creates a sparse VM disk,
and installs Apptainer inside the VM. This can take several minutes.
EOF

  if ! command -v limactl >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
      echo "Installing Lima with Homebrew ..."
      brew install lima
    elif install_lima_macos_local "$root"; then
      :
    else
      cat >&2 <<EOF
Lima is required for automatic macOS setup.

The installer tried a repo-local Lima binary install and could not complete it.
Install Lima with one of the official options, then rerun this installer:
  brew install lima
  # or MacPorts/Nix/binary archive from https://lima-vm.io/docs/installation/
  ./scripts/install.sh --mode ${MODE:-local}
EOF
      exit 1
    fi
  fi

  if ! limactl list --format '{{.Name}}' 2>/dev/null | grep -qx 'apptainer'; then
    prompt_lima_vm_size_macos "$root"
    echo "Creating the Lima Apptainer VM with ${LIMA_MEMORY_GIB} GiB RAM, ${LIMA_CPUS} CPUs, and ${LIMA_DISK_GIB} GiB sparse disk ..."
    if ! limactl start --name=apptainer --memory "$LIMA_MEMORY_GIB" --cpus "$LIMA_CPUS" --disk "$LIMA_DISK_GIB" template:apptainer; then
      echo "Lima reported a startup error; checking whether the VM is still running." >&2
    fi
  elif ! lima_instance_running; then
    echo "Starting existing Lima Apptainer VM ..."
    if ! limactl start apptainer; then
      echo "Lima reported a startup error; checking whether the VM is still running." >&2
    fi
  fi

  echo "Waiting for Apptainer inside Lima ..."
  local deadline=$((SECONDS + timeout))
  local fallback_at=$((SECONDS + fallback_after))
  local last_notice=0
  local tried_release_fallback=0
  until limactl shell apptainer apptainer --version >/dev/null 2>&1; do
    if [[ "$tried_release_fallback" -eq 0 ]] && (( SECONDS >= fallback_at )); then
      tried_release_fallback=1
      echo "Lima template provisioning is still waiting; trying the Apptainer GitHub release package fallback ..."
      limactl shell apptainer bash -lc "cd /tmp && curl -fL -o apptainer_${release_version}_amd64.deb https://github.com/apptainer/apptainer/releases/download/v${release_version}/apptainer_${release_version}_amd64.deb && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ./apptainer_${release_version}_amd64.deb" || true
    fi
    if (( SECONDS >= deadline )); then
      cat >&2 <<EOF
Timed out waiting for Apptainer inside the Lima VM.

The VM is managed by Lima as "apptainer". Useful diagnostics:
  limactl list
  limactl shell apptainer cloud-init status --long
  limactl shell apptainer sudo tail -n 120 /var/log/cloud-init-output.log

If the VM package install is stuck on the Apptainer PPA, the installer also
tries the official GitHub release package. Rerun this installer after the
network recovers. The partially created VM will be reused.
EOF
      exit 1
    fi
    if (( SECONDS - last_notice >= 30 )); then
      echo "Still waiting for the Lima Apptainer package install ..."
      last_notice=$SECONDS
    fi
    sleep 5
  done

  ensure_lima_checkout_mount "$root"

  mkdir -p "$wrapper_dir"
  cat >"$wrapper" <<'EOF'
#!/usr/bin/env bash
exec limactl shell apptainer apptainer "$@"
EOF
  chmod 755 "$wrapper"
  export APPTAINER_BIN="$wrapper"
  cat <<EOF
Created Apptainer wrapper: $wrapper

Note: macOS runs Apptainer inside Lima. If bind mounts under this checkout are
not writable from the VM, move the checkout to Lima's writable shared directory
or adjust the Lima mount settings.
EOF
}

ensure_apptainer() {
  local root="$1"
  local apptainer_path
  if apptainer_path="$(command -v "${APPTAINER_BIN:-apptainer}" 2>/dev/null)"; then
    if [[ "$(uname -s 2>/dev/null || true)" == "Darwin" && "$apptainer_path" == "$root/.apptainer/bin/apptainer" ]]; then
      ensure_lima_checkout_mount "$root"
    fi
    log_step "Apptainer available at $apptainer_path"
    return 0
  fi

  if [[ "${INSTALL_PREREQS:-1}" -ne 1 ]]; then
    cat >&2 <<EOF
Apptainer is required but was not found.
Re-run without --no-prereqs to let the installer prepare it, or set APPTAINER_BIN.
EOF
    exit 1
  fi

  case "$(uname -s 2>/dev/null || true)" in
    Linux)
      install_apptainer_linux "$root"
      ;;
    Darwin)
      install_apptainer_macos "$root"
      ;;
    *)
      echo "Apptainer is missing and automatic installation is not supported on this OS." >&2
      exit 1
      ;;
  esac
}
