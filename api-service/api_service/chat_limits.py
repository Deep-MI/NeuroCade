"""Provide API service chat limits behavior for NeuroCade."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from api_service.runtime import settings


class ChatRequestGuard:
    def __init__(
        self,
        *,
        max_requests_per_window: int,
        window_seconds: int,
        max_concurrent_requests: int,
        max_concurrent_per_key: int,
    ) -> None:
        """Configure per-caller rate limits and concurrent chat request caps.

        Parameters
        ----------
        max_requests_per_window : int
            Maximum accepted requests from one caller during the rolling window.
        window_seconds : int
            Duration of the rolling request window in seconds.
        max_concurrent_requests : int
            Maximum number of chat requests allowed across all callers.
        max_concurrent_per_key : int
            Maximum number of simultaneous chat requests from one caller.
        """
        self.max_requests_per_window = max(1, int(max_requests_per_window))
        self.window_seconds = max(1, int(window_seconds))
        self.max_concurrent_requests = max(1, int(max_concurrent_requests))
        self.max_concurrent_per_key = max(1, int(max_concurrent_per_key))
        self._lock = asyncio.Lock()
        self._request_history: dict[str, deque[float]] = defaultdict(deque)
        self._inflight_by_key: dict[str, int] = defaultdict(int)
        self._inflight_threads: set[str] = set()
        self._global_inflight = 0

    def _normalize_key(self, key: str | None) -> str:
        """Return a non-empty caller key for rate-limit tracking."""
        return str(key or "anonymous").strip() or "anonymous"

    def _prune_history(self, history: deque[float], now: float) -> None:
        """Remove request timestamps that are outside the rolling window."""
        cutoff = now - self.window_seconds
        while history and history[0] <= cutoff:
            history.popleft()

    async def acquire(self, key: str | None, *, thread_key: str | None = None) -> str:
        """Reserve capacity for a chat request or raise HTTP 429.

        Parameters
        ----------
        key : str | None
            Caller identifier used for per-key rate and concurrency limits.

        Returns
        -------
        str
            Normalized caller key to pass back to :meth:`release`.
        """
        normalized_key = self._normalize_key(key)
        now = time.monotonic()
        async with self._lock:
            history = self._request_history[normalized_key]
            self._prune_history(history, now)
            if len(history) >= self.max_requests_per_window:
                raise HTTPException(status_code=429, detail="Too many chat requests. Please wait and retry.")
            if self._global_inflight >= self.max_concurrent_requests:
                raise HTTPException(status_code=429, detail="Chat service is busy. Please retry shortly.")
            if self._inflight_by_key[normalized_key] >= self.max_concurrent_per_key:
                raise HTTPException(status_code=429, detail="Too many concurrent chat requests for this caller.")
            if thread_key and thread_key in self._inflight_threads:
                raise HTTPException(status_code=409, detail="Another assistant turn is already running in this chat.")

            history.append(now)
            self._global_inflight += 1
            self._inflight_by_key[normalized_key] += 1
            if thread_key:
                self._inflight_threads.add(thread_key)
        return normalized_key

    async def release(self, key: str | None, *, thread_key: str | None = None) -> None:
        """Release a previously acquired chat request slot.

        Parameters
        ----------
        key : str | None
            Caller key returned by :meth:`acquire`.
        """
        normalized_key = self._normalize_key(key)
        async with self._lock:
            if thread_key:
                self._inflight_threads.discard(thread_key)
            inflight = self._inflight_by_key.get(normalized_key, 0)
            if inflight <= 0:
                return
            if self._global_inflight > 0:
                self._global_inflight -= 1
            if inflight <= 1:
                self._inflight_by_key.pop(normalized_key, None)
            else:
                self._inflight_by_key[normalized_key] = inflight - 1


chat_request_guard = ChatRequestGuard(
    max_requests_per_window=settings.chat_max_requests_per_window,
    window_seconds=settings.chat_rate_limit_window_seconds,
    max_concurrent_requests=settings.chat_max_concurrent_requests,
    max_concurrent_per_key=settings.chat_max_concurrent_per_key,
)
