"""Contracts for the background status icon owned by scripts/lib/tray.sh.

A detached launch in a desktop session shows a menu-bar / top-bar icon. The
launcher decides whether that is possible (desktop session, installed
Electron, opt-out) and owns the icon's PID file, so a restart never stacks a
second icon and a stop never leaves one behind. A stub Electron binary stands
in for the real one.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS = (REPO_ROOT / "scripts/lib/processes.sh", REPO_ROOT / "scripts/lib/tray.sh")

_FAKE_ELECTRON = """#!/usr/bin/env bash
trap 'exit 0' TERM
printf '%s\\n' "$*" >>"$FAKE_ELECTRON_ARGS"
while :; do sleep 0.2; done
"""


def _fake_root(tmp_path: Path, *, electron: bool = True) -> Path:
    root = tmp_path / "root"
    (root / ".runtime").mkdir(parents=True)
    (root / "client/electron").mkdir(parents=True)
    (root / "client/electron/tray.mjs").write_text("// stub\n", encoding="utf-8")
    if electron:
        package = root / "client/node_modules/electron"
        (package / "dist").mkdir(parents=True)
        binary = package / "dist/electron"
        binary.write_text(_FAKE_ELECTRON, encoding="utf-8")
        binary.chmod(0o755)
        (package / "path.txt").write_text("electron", encoding="utf-8")
    return root


def _run(root: Path, script: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(root)),
        "ROOT_DIR": str(root),
        "RUNTIME_DIR": str(root / ".runtime"),
        "BRIDGE_PORT": "8765",
        "FAKE_ELECTRON_ARGS": str(root / "electron-args.txt"),
        # Default to a Linux desktop session; tests override what they probe.
        "TRAY_PLATFORM": "Linux",
        "DISPLAY": ":0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/bus",
    }
    env.update(env_overrides)
    env = {key: value for key, value in env.items() if value != "<unset>"}
    sources = " ".join(f'source "{lib}";' for lib in LIBS)
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail; {sources} {script}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _should_start(root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return _run(root, "tray_should_start", **env)


def _tray_pid(root: Path) -> int:
    return int((root / ".runtime/tray.pid").read_text(encoding="utf-8").splitlines()[0])


def _electron_args(root: Path, timeout: float = 10.0) -> str:
    """Arguments the stub Electron recorded; it writes them after it starts."""
    args_file = root / "electron-args.txt"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if args_file.exists() and args_file.read_text(encoding="utf-8").endswith("\n"):
            return args_file.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError("the stub Electron never started")


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_linux_desktop_session_with_electron_shows_the_icon(tmp_path: Path) -> None:
    assert _should_start(_fake_root(tmp_path)).returncode == 0


def test_wayland_session_counts_as_a_desktop(tmp_path: Path) -> None:
    result = _should_start(_fake_root(tmp_path), DISPLAY="<unset>", WAYLAND_DISPLAY="wayland-0")
    assert result.returncode == 0


def test_linux_without_a_display_is_headless(tmp_path: Path) -> None:
    result = _should_start(_fake_root(tmp_path), DISPLAY="<unset>")
    assert result.returncode != 0
    assert result.stderr == ""


def test_linux_without_a_session_bus_cannot_host_an_indicator(tmp_path: Path) -> None:
    result = _should_start(
        _fake_root(tmp_path),
        DBUS_SESSION_BUS_ADDRESS="<unset>",
        XDG_RUNTIME_DIR=str(tmp_path / "no-runtime-dir"),
    )
    assert result.returncode != 0


def test_macos_login_session_shows_the_icon(tmp_path: Path) -> None:
    result = _should_start(_fake_root(tmp_path), TRAY_PLATFORM="Darwin", DISPLAY="<unset>")
    assert result.returncode == 0


def test_macos_over_ssh_does_not(tmp_path: Path) -> None:
    result = _should_start(_fake_root(tmp_path), TRAY_PLATFORM="Darwin", SSH_CONNECTION="10.0.0.1 22 10.0.0.2 22")
    assert result.returncode != 0


def test_other_platforms_never_show_the_icon(tmp_path: Path) -> None:
    assert _should_start(_fake_root(tmp_path), TRAY_PLATFORM="FreeBSD").returncode != 0


def test_missing_electron_download_skips_quietly_by_default(tmp_path: Path) -> None:
    result = _should_start(_fake_root(tmp_path, electron=False))
    assert result.returncode != 0
    assert result.stderr == ""


def test_explicit_request_explains_a_skip(tmp_path: Path) -> None:
    result = _should_start(_fake_root(tmp_path, electron=False), NEUROCADE_TRAY="true")
    assert result.returncode != 0
    assert "install-electron" in result.stderr


def test_opt_out_and_invalid_values_never_show_the_icon(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    assert _should_start(root, NEUROCADE_TRAY="false").returncode != 0
    invalid = _should_start(root, NEUROCADE_TRAY="sometimes")
    assert invalid.returncode != 0
    assert "NEUROCADE_TRAY must be" in invalid.stderr


def test_start_records_one_icon_and_stop_removes_it(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    started = _run(root, "start_tray")
    assert started.returncode == 0, started.stderr
    assert "Status icon" in started.stdout
    pid = _tray_pid(root)
    try:
        assert _run(root, "tray_running").returncode == 0
        args = _electron_args(root)
        assert "--no-sandbox" in args
        assert str(root / "client/electron/tray.mjs") in args

        # A second detached start (e.g. `start -d` on a running app) reuses it.
        again = _run(root, "start_tray")
        assert again.returncode == 0, again.stderr
        assert _tray_pid(root) == pid
        assert _electron_args(root).count("tray.mjs") == 1

        stopped = _run(root, "stop_tray")
        assert stopped.returncode == 0, stopped.stderr
        assert _wait_gone(pid)
        assert not (root / ".runtime/tray.pid").exists()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def test_macos_launch_keeps_the_chromium_sandbox(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    started = _run(root, "start_tray", TRAY_PLATFORM="Darwin", DISPLAY="<unset>")
    assert started.returncode == 0, started.stderr
    pid = _tray_pid(root)
    try:
        assert "--no-sandbox" not in _electron_args(root)
        assert "menu bar" in started.stdout
    finally:
        _run(root, "stop_tray")
        _wait_gone(pid)


def test_opt_out_start_launches_nothing(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    result = _run(root, "start_tray", NEUROCADE_TRAY="false")
    assert result.returncode == 0
    assert not (root / ".runtime/tray.pid").exists()


def test_stop_without_an_icon_succeeds(tmp_path: Path) -> None:
    assert _run(_fake_root(tmp_path), "stop_tray").returncode == 0


def test_stop_refuses_to_kill_an_unrelated_process_in_the_pid_file(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        (root / ".runtime/tray.pid").write_text(f"{unrelated.pid}\n", encoding="utf-8")
        result = _run(root, "stop_tray")
        assert result.returncode != 0
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="process start times come from Linux /proc")
def test_recorded_start_time_survives_reparenting(tmp_path: Path) -> None:
    """A detached process outlives its launcher; its identity must not change."""
    pid_file = tmp_path / "child.pid"
    script = f'source "{LIBS[0]}"; nohup sleep 30 >/dev/null 2>&1 </dev/null & pid=$!; write_pid_file "$pid" "{pid_file}"; echo "$pid"'
    launcher = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    pid = int(launcher.stdout.strip())
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_time = stat.rsplit(") ", 1)[1].split()[19]
        assert pid_file.read_text(encoding="utf-8").splitlines()[1] == start_time
        # The launching shell has exited, so the child now has another parent.
        check = subprocess.run(
            ["bash", "-c", f'source "{LIBS[0]}"; pid_matches "{pid_file}" sleep'],
            check=False,
        )
        assert check.returncode == 0
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)
