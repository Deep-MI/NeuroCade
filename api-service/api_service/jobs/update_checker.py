"""In-process periodic update checking for NeuroCade.

Runs the ``scripts/update_checker.py`` logic on a daemon thread inside the app
process. Failures are swallowed—a missing network or endpoint must never affect
the app.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path

from backend_common.settings import ROOT_DIR

logger = logging.getLogger(__name__)

_SCRIPT = Path(ROOT_DIR) / "scripts" / "update_checker.py"
_MIN_INTERVAL_SECONDS = 3600


def _load_script_module():
    spec = importlib.util.spec_from_file_location("neurocade_update_checker", _SCRIPT)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_update_checker() -> threading.Thread | None:
    """Start the background update-check thread; returns it, or None if unavailable."""
    if not _SCRIPT.is_file():
        return None
    module = _load_script_module()
    if module is None:
        return None

    env_path = Path(ROOT_DIR) / ".env"

    def _loop() -> None:
        while True:
            interval = module.DEFAULT_INTERVAL_SECONDS
            try:
                env_values = module.parse_env_file(env_path)
                interval = int(
                    module.config(
                        "NEUROCADE_UPDATE_CHECK_INTERVAL_SECONDS",
                        env_values,
                        str(module.DEFAULT_INTERVAL_SECONDS),
                    )
                )
                module.check_once(env_values)
            except Exception:
                logger.debug("update_check.failed", exc_info=True)
            time.sleep(max(_MIN_INTERVAL_SECONDS, interval))

    thread = threading.Thread(target=_loop, name="update-checker", daemon=True)
    thread.start()
    logger.info("update_checker.started")
    return thread
