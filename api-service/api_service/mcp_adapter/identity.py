"""Persistent installation identity, separate from process launch diagnostics."""

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

from backend_common.settings import get_settings


def installation_id(data_root: Path | None = None) -> str:
    root = Path(data_root) if data_root is not None else get_settings().fs_data_root
    root.mkdir(parents=True, exist_ok=True)
    target = root / ".mcp-installation-id"
    if not target.exists():
        # Publish a complete file atomically; concurrent first requests share the winner.
        descriptor, temporary = tempfile.mkstemp(prefix=".mcp-identity-", dir=root)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(str(uuid4()) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(FileExistsError):
                os.link(temporary, target)
        finally:
            os.unlink(temporary)
    if target.is_symlink() or not target.is_file():
        raise ValueError("Invalid installation identity file")
    value = target.read_text().strip()
    if str(UUID(value)) != value:
        raise ValueError("Invalid installation identity")
    return value
