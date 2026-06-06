#!/usr/bin/env bash
# Purpose:
#   Supports the NeuroCade installer lima workflow.


lima_instance_running() {
  limactl list --format '{{.Name}} {{.Status}}' 2>/dev/null | awk '$1 == "apptainer" && $2 == "Running" { found = 1 } END { exit(found ? 0 : 1) }'
}

lima_checkout_mount_live_writable() {
  local root="$1"
  lima_instance_running || return 1
  limactl shell apptainer sh -c '
root="$1"
mkdir -p "$root/.runtime" || exit 1
probe="$root/.runtime/.neurocade-lima-write-probe.$$"
: >"$probe" && rm -f "$probe"
' sh "$root" >/dev/null 2>&1
}

restart_lima_for_checkout_mount() {
  local root="$1"
  echo "Restarting Lima Apptainer VM so the checkout mount is writable ..."
  limactl stop apptainer
  limactl start apptainer
  if ! lima_checkout_mount_live_writable "$root"; then
    cat >&2 <<EOF
Lima restarted, but the NeuroCade checkout is still not writable inside the VM:
  $root

Check ~/.lima/apptainer/lima.yaml and make sure this checkout is mounted with:
  - location: "$root"
    writable: true
EOF
    return 1
  fi
}

prepend_local_lima() {
  local root="$1"
  local lima_bin="$root/$LOCAL_LIMA_DIR_REL/bin"
  if [[ -x "$lima_bin/limactl" ]]; then
    export PATH="$lima_bin:$PATH"
  fi
}

install_lima_macos_local() {
  local root="$1"
  local prefix="$root/$LOCAL_LIMA_DIR_REL"
  local uname_m arch version tag base_url tmp_dir archive filename sums_url additional_filename

  prepend_local_lima "$root"
  if command -v limactl >/dev/null 2>&1; then
    return 0
  fi

  uname_m="$(uname -m 2>/dev/null || true)"
  case "$uname_m" in
    x86_64|amd64) arch="x86_64" ;;
    arm64|aarch64) arch="arm64" ;;
    *)
      echo "Automatic Lima install is not supported on macOS architecture: ${uname_m:-unknown}" >&2
      return 1
      ;;
  esac

  version="${LIMA_VERSION:-}"
  if [[ -z "$version" ]]; then
    version="$(curl -fsSL https://api.github.com/repos/lima-vm/lima/releases/latest | sed -n 's/.*"tag_name":[[:space:]]*"v\([^"]*\)".*/\1/p' | head -n 1)"
  fi
  if [[ -z "$version" ]]; then
    echo "Could not determine the latest Lima release version." >&2
    return 1
  fi
  version="${version#v}"
  tag="v$version"
  filename="lima-${version}-Darwin-${arch}.tar.gz"
  additional_filename="lima-additional-guestagents-${version}-Darwin-${arch}.tar.gz"
  base_url="https://github.com/lima-vm/lima/releases/download/$tag"
  sums_url="$base_url/SHA256SUMS"

  echo "Installing Lima locally under $prefix ..."
  tmp_dir="$(mktemp -d)"
  archive="$tmp_dir/$filename"
  mkdir -p "$prefix"
  download_with_sha256 "$base_url/$filename" "$sums_url" "$filename" "$archive"
  tar -xzf "$archive" -C "$prefix"
  if curl -fsSL "$base_url/$additional_filename" -o "$tmp_dir/$additional_filename"; then
    tar -xzf "$tmp_dir/$additional_filename" -C "$prefix"
  else
    echo "Lima additional guest agents archive was not available; continuing with the main archive."
  fi
  rm -rf "$tmp_dir"
  prepend_local_lima "$root"
  if ! command -v limactl >/dev/null 2>&1; then
    echo "Lima installer completed, but limactl was not found under $prefix/bin." >&2
    return 1
  fi
  echo "Lima installed: $(command -v limactl)"
}

ensure_lima_checkout_mount() {
  local root="$1"
  local yaml="$HOME/.lima/apptainer/lima.yaml"
  local config_changed=0
  [[ -f "$yaml" ]] || return 0
  if ! awk -v root="$root" '
    $0 == "- location: \"" root "\"" {
      found = 1
      in_target = 1
      next
    }
    in_target && /^- location: / {
      in_target = 0
    }
    in_target && /^[[:space:]]*writable:[[:space:]]*true[[:space:]]*$/ {
      writable = 1
    }
    END {
      exit(found && writable ? 0 : 1)
    }
  ' "$yaml"; then

    local backup="$yaml.neurocade-backup.$(date +%Y%m%d%H%M%S)"
    local tmp
    tmp="$(mktemp)"
    cp "$yaml" "$backup"
    awk -v root="$root" '
    function add_checkout_mount() {
      print "- location: \"" root "\""
      print "  writable: true"
      added = 1
    }
    function finish_target() {
      if (in_target && !target_had_writable) {
        print "  writable: true"
      }
      in_target = 0
    }
    BEGIN { in_mounts = 0; found = 0; added = 0; in_target = 0; target_had_writable = 0 }
    /^mounts:/ {
      in_mounts = 1
      print
      next
    }
    $0 == "- location: \"" root "\"" {
      finish_target()
      found = 1
      in_target = 1
      target_had_writable = 0
      print
      next
    }
    in_target && /^- location: / {
      finish_target()
    }
    in_target && /^[[:space:]]*writable:/ {
      print "  writable: true"
      target_had_writable = 1
      next
    }
    in_mounts && !found && !added && /^#/ {
      add_checkout_mount()
      print
      next
    }
    { print }
    END {
      finish_target()
      if (in_mounts && !found && !added) {
        add_checkout_mount()
      }
    }
    ' "$yaml" >"$tmp"
    mv "$tmp" "$yaml"
    config_changed=1
    echo "Configured writable Lima mount for this checkout: $root"
    echo "Backed up previous Lima config to $backup"
  fi

  if lima_instance_running; then
    if [[ "$config_changed" -eq 1 ]] || ! lima_checkout_mount_live_writable "$root"; then
      restart_lima_for_checkout_mount "$root"
    fi
  fi
}
