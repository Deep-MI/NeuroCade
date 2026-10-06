"""Verify matched bridge dependency caching and its explicit repair path."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("wheel", [False, True])
@pytest.mark.parametrize("mcp", [False, True])
def test_unchanged_inputs_skip_install_but_changes_missing_binary_and_repair_install(tmp_path, wheel, mcp):
    package = tmp_path / ("bridge.whl" if wheel else "bridge")
    if wheel:
        package.write_text("wheel contents")
        change_file = package
    else:
        package.mkdir()
        change_file = package / "pyproject.toml"
        change_file.write_text('[project]\nname="test"\n')
    mcp_package = tmp_path / "packages/neurocade-mcp"
    mcp_package.mkdir(parents=True)
    (mcp_package / "pyproject.toml").write_text("mcp source")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/package_fingerprint.py", scripts)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(sys.executable)
    bridge = venv / "bin/neurocade-runtime-bridge"
    bridge.write_text("#!/bin/sh\nexit 0\n")
    bridge.chmod(0o755)
    launcher = (ROOT / "scripts/run.sh").read_text()
    functions = launcher[launcher.index("ensure_bridge_environment() {"):launcher.index("report_managed_toolchain() {")]
    calls = tmp_path / "calls"
    script = r'''
set -euo pipefail
ROOT_DIR="$1"; BRIDGE_VENV="$2"; BRIDGE_PACKAGE="$3"; CALLS="$4"; TEST_PYTHON="$5"; CHANGE_FILE="$6"
BRIDGE_BIN="$BRIDGE_VENV/bin/neurocade-runtime-bridge"; MCP_ENABLED="$7"; NEUROCADE_PYTHON_VERSION=3.12
require_managed_uv() { return 0; }
managed_python_path() { printf '%s\n' "$TEST_PYTHON"; }
managed_uv() {
  printf '%s\n' "$*" >>"$CALLS"
  printf '#!/bin/sh\nexit 0\n' >"$BRIDGE_BIN"; chmod +x "$BRIDGE_BIN"
  cp "$BRIDGE_BIN" "$BRIDGE_VENV/bin/neurocade-mcp"
}
fail() { echo "$*" >&2; exit 1; }
''' + functions + r'''
ensure_bridge_environment
ensure_bridge_environment
printf '\n# changed\n' >>"$CHANGE_FILE"
ensure_bridge_environment
rm "$BRIDGE_BIN"
ensure_bridge_environment
if [[ "$MCP_ENABLED" == true ]]; then
  printf "changed" >>"$ROOT_DIR/packages/neurocade-mcp/pyproject.toml"
  ensure_bridge_environment
fi
COMMAND=repair ensure_bridge_environment
'''
    result = subprocess.run(["bash", "-c", script, "bridge-cache", str(tmp_path), str(venv), str(package),
                             str(calls), sys.executable, str(change_file), str(mcp).lower()], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    installs = calls.read_text().splitlines()
    assert len(installs) == (7 if mcp else 4)
    assert all("pip install --reinstall" in call for call in installs)
