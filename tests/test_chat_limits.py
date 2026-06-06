"""Test chat limits behavior for NeuroCade."""

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.chat_limits import ChatRequestGuard  # noqa: E402


@pytest.mark.asyncio
async def test_chat_request_guard_limits_requests_per_window():
    """Verify chat request guard limits requests per window.

    Returns
    -------
    None
        This function does not return a value.
    """
    guard = ChatRequestGuard(
        max_requests_per_window=1,
        window_seconds=60,
        max_concurrent_requests=4,
        max_concurrent_per_key=2,
    )

    key = await guard.acquire("user-1")
    await guard.release(key)

    with pytest.raises(HTTPException) as exc_info:
        await guard.acquire("user-1")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many chat requests. Please wait and retry."


@pytest.mark.asyncio
async def test_chat_request_guard_limits_per_key_concurrency():
    """Verify chat request guard limits per key concurrency.

    Returns
    -------
    None
        This function does not return a value.
    """
    guard = ChatRequestGuard(
        max_requests_per_window=10,
        window_seconds=60,
        max_concurrent_requests=4,
        max_concurrent_per_key=1,
    )

    key = await guard.acquire("user-1")

    try:
        with pytest.raises(HTTPException) as exc_info:
            await guard.acquire("user-1")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "Too many concurrent chat requests for this caller."
    finally:
        await guard.release(key)


@pytest.mark.asyncio
async def test_chat_request_guard_limits_global_concurrency():
    """Verify chat request guard limits global concurrency.

    Returns
    -------
    None
        This function does not return a value.
    """
    guard = ChatRequestGuard(
        max_requests_per_window=10,
        window_seconds=60,
        max_concurrent_requests=1,
        max_concurrent_per_key=1,
    )

    key = await guard.acquire("user-1")

    try:
        with pytest.raises(HTTPException) as exc_info:
            await guard.acquire("user-2")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "Chat service is busy. Please retry shortly."
    finally:
        await guard.release(key)


@pytest.mark.asyncio
async def test_chat_request_guard_double_release_does_not_free_global_slot():
    """Verify chat request guard double release does not free global slot.

    Returns
    -------
    None
        This function does not return a value.
    """
    guard = ChatRequestGuard(
        max_requests_per_window=10,
        window_seconds=60,
        max_concurrent_requests=1,
        max_concurrent_per_key=2,
    )

    key = await guard.acquire("user-1")
    await guard.release(key)
    await guard.release(key)

    second_key = await guard.acquire("user-2")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await guard.acquire("user-3")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "Chat service is busy. Please retry shortly."
    finally:
        await guard.release(second_key)
