"""Importable release checks and a stoppable application-owned background service."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend_common.settings import ROOT_DIR

logger = logging.getLogger(__name__)
_MIN_INTERVAL_SECONDS = 3600

DEFAULT_URL = "https://NeuroCade.org/latest.json"
DEFAULT_INTERVAL_SECONDS = 86_400
CONFIG_KEYS = {
    "NEUROCADE_VERSION",
    "NEUROCADE_VERSION_CHECK_URL",
    "NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS",
}
PLACEHOLDER_VERSIONS = frozenset({"", "0.0.0", "dev", "unknown"})


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in CONFIG_KEYS or key in os.environ:
            continue
        values[key] = unquote_env_value(value.strip())
    return values


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return (
            value[1:-1]
            .replace(r"\\", "\\")
            .replace(r"\"", '"')
            .replace(r"\$", "$")
            .replace(r"\`", "`")
        )
    return value


def config(name: str, env_file_values: dict[str, str], default: str = "") -> str:
    return os.environ.get(name) or env_file_values.get(name) or default


def current_version(env_file_values: dict[str, str]) -> str:
    configured = config("NEUROCADE_VERSION", env_file_values)
    if configured.lower() not in PLACEHOLDER_VERSIONS:
        return configured
    build_version = os.environ.get("NEUROCADE_BUILD_VERSION", "").strip()
    if build_version.lower() not in PLACEHOLDER_VERSIONS:
        return build_version
    try:
        package = json.loads((ROOT_DIR / "client" / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(package.get("version") or "").strip()


def version_parts(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", version))
    return parts or (0,)


def is_newer(candidate: str, installed: str) -> bool:
    left = version_parts(candidate)
    right = version_parts(installed)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def fetch_payload(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def check_once(env_file_values: dict[str, str]) -> None:
    current = current_version(env_file_values)
    if not current:
        return
    url = config("NEUROCADE_VERSION_CHECK_URL", env_file_values, DEFAULT_URL)
    payload = fetch_payload(url)
    if not payload:
        return

    latest = str(payload.get("version") or payload.get("latest_version") or payload.get("tag") or "").strip()
    if not latest or not is_newer(latest, current):
        return

    update_url = str(payload.get("url") or payload.get("html_url") or payload.get("download_url") or "").strip()
    message = str(payload.get("message") or payload.get("notes") or "").strip()
    line = f"[NeuroCade update] Version {latest} is available; current version is {current}."
    if update_url:
        line += f" {update_url}"
    if message:
        line += f" {message}"
    print(line, flush=True)


@dataclass(frozen=True)
class UpdateChecker:
    thread: threading.Thread
    stopped: threading.Event

    def stop(self, timeout: float = 5.0) -> None:
        """Wake the periodic wait and allow any bounded network request to finish."""
        self.stopped.set()
        self.thread.join(timeout=timeout)


def start_update_checker() -> UpdateChecker:
    """Start the checker; the application lifespan owns and stops its handle."""
    stopped = threading.Event()
    env_path = Path(os.environ.get("ENV_FILE", ROOT_DIR / ".env"))

    def check_loop() -> None:
        while not stopped.is_set():
            interval = DEFAULT_INTERVAL_SECONDS
            try:
                values = parse_env_file(env_path)
                interval = int(config("NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS", values, str(interval)))
                check_once(values)
            except Exception:
                logger.debug("update_check.failed", exc_info=True)
            stopped.wait(max(_MIN_INTERVAL_SECONDS, interval))

    thread = threading.Thread(target=check_loop, name="update-checker", daemon=True)
    handle = UpdateChecker(thread, stopped)
    thread.start()
    return handle
