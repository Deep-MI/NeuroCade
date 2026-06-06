"""Small filesystem helpers shared by local runtime code."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def safe_write_json(path: str, data: dict) -> None:
    """Write JSON data via a temporary file and atomically replace the target."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except OSError as exc:
        logger.error("Failed to write %s: %s", path, exc)
