#!/usr/bin/env python3
"""Log when a newer NeuroCade version is available."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://NeuroCade.org/latest.json"
DEFAULT_INTERVAL_SECONDS = 86_400
CONFIG_KEYS = {
    "NEUROCADE_VERSION",
    "NEUROCADE_VERSION_CHECK_URL",
    "NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS",
}


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
    if configured:
        return configured
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether a newer NeuroCade version is available.")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    args = parser.parse_args()

    env_file = Path(os.environ.get("ENV_FILE", ROOT_DIR / ".env"))
    env_file_values = parse_env_file(env_file)
    interval = int(config("NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS", env_file_values, str(DEFAULT_INTERVAL_SECONDS)))

    while True:
        check_once(env_file_values)
        if args.once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
