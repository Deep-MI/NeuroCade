"""Contracts that keep a launch session recoverable.

The launcher owns two irreversible decisions: whether the application it
manages is already running, and whether it may surrender its launch session.
Getting either wrong strands an installation in a state no later command can
repair, so both are pinned here against a stubbed container runtime.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DRIVER = REPO_ROOT / "scripts/lib/runtime_docker.sh"

# The stub answers from files so each test states container reality directly.
_DOCKER_STUB = """
state="$STUB_STATE"
case "$1" in
  info)
    [[ -f "$state/daemon" ]] || exit 1
    ;;
  container)
    [[ -f "$state/daemon" ]] || exit 1
    [[ -f "$state/container" ]] || exit 1
    if [[ "$3" == "--format" ]]; then
      running=false
      [[ -f "$state/running" ]] && running=true
      printf '%s %s\\n' "$running" "$(cat "$state/label" 2>/dev/null)"
    fi
    ;;
  stop|rm)
    [[ -f "$state/unstoppable" ]] || rm -f "$state/container" "$state/running"
    ;;
  *)
    ;;
esac
exit 0
"""


def _container_state(
    tmp_path: Path,
    *,
    daemon: bool = True,
    exists: bool = True,
    running: bool = True,
    label: str = "launch-a",
    unstoppable: bool = False,
) -> Path:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    if daemon:
        (state / "daemon").touch()
    if exists:
        (state / "container").touch()
    if running:
        (state / "running").touch()
    if unstoppable:
        (state / "unstoppable").touch()
    (state / "label").write_text(label, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(f"#!/usr/bin/env bash\n{_DOCKER_STUB}", encoding="utf-8")
    stub.chmod(0o755)
    return state


def _call(tmp_path: Path, function: str, *, launch_id: str = "launch-a") -> int:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{env['PATH']}",
            "STUB_STATE": str(tmp_path / "state"),
            "CONTAINER_NAME": "neurocade-test",
            "LAUNCH_ID": launch_id,
        }
    )
    result = subprocess.run(
        ["bash", "-c", f'source "$1"; {function}', "driver-test", str(DOCKER_DRIVER)],
        text=True,
        capture_output=True,
        env=env,
    )
    return result.returncode


def test_running_application_is_recognized_by_its_launch_label(tmp_path: Path) -> None:
    _container_state(tmp_path, label="launch-a")
    assert _call(tmp_path, "runtime_application_running", launch_id="launch-a") == 0


def test_foreign_container_on_the_same_name_is_not_our_application(tmp_path: Path) -> None:
    """A responder from another launch session must never be adopted."""
    _container_state(tmp_path, label="someone-elses-launch")
    assert _call(tmp_path, "runtime_application_running", launch_id="launch-a") == 1


def test_stopped_container_is_not_reported_as_running(tmp_path: Path) -> None:
    _container_state(tmp_path, running=False)
    assert _call(tmp_path, "runtime_application_running") == 1


def test_missing_launch_id_never_claims_a_running_application(tmp_path: Path) -> None:
    """Without an identity there is nothing to compare, so nothing is ours."""
    _container_state(tmp_path)
    assert _call(tmp_path, "runtime_application_running", launch_id="") == 1


def test_stop_reports_success_when_no_container_exists(tmp_path: Path) -> None:
    _container_state(tmp_path, exists=False, running=False)
    assert _call(tmp_path, "runtime_stop_application") == 0


def test_stop_reports_success_once_the_container_is_gone(tmp_path: Path) -> None:
    state = _container_state(tmp_path)
    assert _call(tmp_path, "runtime_stop_application") == 0
    assert not (state / "container").exists()


def test_unreachable_daemon_is_not_reported_as_a_successful_stop(tmp_path: Path) -> None:
    """A down daemon proves nothing; a restart policy may still restore the app.

    Reporting success here is what lets `stop` delete the launch session while
    the application comes back, which no later command can repair.
    """
    _container_state(tmp_path, daemon=False)
    assert _call(tmp_path, "runtime_stop_application") == 1


def test_surviving_container_is_reported_as_a_failed_stop(tmp_path: Path) -> None:
    _container_state(tmp_path, unstoppable=True)
    assert _call(tmp_path, "runtime_stop_application") == 1
