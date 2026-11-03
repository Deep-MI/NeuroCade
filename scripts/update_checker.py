#!/usr/bin/env python3
"""Command-line entry point for the application's shared update checker."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "api-service"))
sys.path.insert(0, str(ROOT_DIR))

from api_service.jobs.update_checker import (  # noqa: E402
    DEFAULT_INTERVAL_SECONDS,
    check_once,
    config,
    parse_env_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    values = parse_env_file(Path(os.environ.get("ENV_FILE", ROOT_DIR / ".env")))
    while True:
        check_once(values)
        if args.once:
            return
        time.sleep(max(3600, int(config("NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS", values, str(DEFAULT_INTERVAL_SECONDS)))))


if __name__ == "__main__":
    main()
