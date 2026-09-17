#!/usr/bin/env bash

provenance_value() {
  local root="$1" key="$2" file="$root/.runtime/provenance.json" python_bin
  [[ -s "$file" ]] || return 0
  python_bin="$(managed_python_path 2>/dev/null || command -v python3 || true)"
  [[ -n "$python_bin" ]] || return 0
  "$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$file" "$key" 2>/dev/null
}

write_provenance() {
  local root="$1" version="$2" revision="$3" channel="$4" artifact="$5" python_bin
  python_bin="$(managed_python_path 2>/dev/null || command -v python3 || true)"
  [[ -n "$python_bin" ]] || { echo "Python is required to record installation provenance." >&2; return 1; }
  "$python_bin" - "$root/.runtime/provenance.json" "$version" "$revision" "$channel" "$artifact" <<'PY'
import json, os, sys, tempfile
path, version, revision, channel, artifact = sys.argv[1:]
payload = {"schema_version": 1, "version": version, "source_revision": revision,
           "channel": channel, "artifact_identity": artifact}
fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as output:
    json.dump(payload, output, indent=2)
    output.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
}
