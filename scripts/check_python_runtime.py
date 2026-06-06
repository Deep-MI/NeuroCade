#!/usr/bin/env python3
"""Check NeuroCade Python runtime dependencies with a local startup sentinel."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import sys
from pathlib import Path


RUNTIME_MODULES = (
    "fastapi",
    "uvicorn",
    "celery",
    "redis",
    "sqlalchemy",
    "psycopg",
    "pydantic_settings",
    "langgraph",
    "langchain_openai",
    "neurocade_runtime_tools",
)


def dependency_key(pyproject: Path) -> str:
    digest = hashlib.sha256(pyproject.read_bytes()).hexdigest()
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return f"python-deps-v1|{sys.executable}|{version}|{digest}"


def sentinel_current(sentinel: Path, key: str) -> bool:
    try:
        return sentinel.read_text(encoding="utf-8").strip() == key
    except OSError:
        return False


def write_sentinel(sentinel: Path, key: str) -> None:
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    temp = sentinel.with_name(f"{sentinel.name}.{id(key)}.tmp")
    temp.write_text(f"{key}\n", encoding="utf-8")
    temp.replace(sentinel)


def check_imports() -> None:
    for module_name in RUNTIME_MODULES:
        importlib.import_module(module_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check NeuroCade Python runtime dependencies.")
    parser.add_argument("--pyproject", required=True, type=Path)
    parser.add_argument("--sentinel", required=True, type=Path)
    args = parser.parse_args()

    key = dependency_key(args.pyproject)
    if sentinel_current(args.sentinel, key):
        print("Python runtime dependencies already verified.")
        return 0

    check_imports()
    write_sentinel(args.sentinel, key)
    print("Python runtime dependencies already installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
