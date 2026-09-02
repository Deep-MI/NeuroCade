"""Manage assistant turns independently from browser stream connections."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from api_service.assistant.activity import AssistantActivity, model_activity

TurnProducer = Callable[[Callable[[str], Awaitable[None]]], Awaitable[None]]
TurnFinalizer = Callable[[], Awaitable[None]]


@dataclass
class ManagedAssistantTurn:
    """One in-process assistant turn and its currently attached subscribers."""

    turn_id: str
    thread_key: str
    started_at: float
    task: asyncio.Task[None] | None = None
    subscribers: set[asyncio.Queue[str | None]] = field(default_factory=set)
    activity: AssistantActivity = field(default_factory=model_activity)


class AssistantTurnManager:
    """Own background assistant tasks beyond individual HTTP connections."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_thread: dict[str, ManagedAssistantTurn] = {}
        self._by_id: dict[str, ManagedAssistantTurn] = {}

    async def start(
        self,
        *,
        turn_id: str,
        thread_key: str,
        producer: TurnProducer,
        finalizer: TurnFinalizer,
    ) -> tuple[ManagedAssistantTurn, asyncio.Queue[str | None]]:
        """Start a turn and attach its initial SSE subscriber atomically."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        async with self._lock:
            active = self._by_thread.get(thread_key)
            if active is not None:
                raise RuntimeError(active.turn_id)
            managed = ManagedAssistantTurn(
                turn_id=turn_id,
                thread_key=thread_key,
                started_at=time.monotonic(),
            )
            managed.subscribers.add(queue)
            self._by_thread[thread_key] = managed
            self._by_id[turn_id] = managed
            managed.task = asyncio.create_task(
                self._run(managed, producer=producer, finalizer=finalizer),
                name=f"assistant-turn-{turn_id}",
            )
        return managed, queue

    async def _run(
        self,
        managed: ManagedAssistantTurn,
        *,
        producer: TurnProducer,
        finalizer: TurnFinalizer,
    ) -> None:
        async def publish(chunk: str) -> None:
            async with self._lock:
                subscribers = tuple(managed.subscribers)
            for queue in subscribers:
                queue.put_nowait(chunk)

        try:
            await producer(publish)
        finally:
            try:
                await finalizer()
            finally:
                async with self._lock:
                    subscribers = tuple(managed.subscribers)
                    managed.subscribers.clear()
                    self._by_thread.pop(managed.thread_key, None)
                    self._by_id.pop(managed.turn_id, None)
                for queue in subscribers:
                    queue.put_nowait(None)

    async def active(self, thread_key: str) -> ManagedAssistantTurn | None:
        """Return the active turn for a private thread, if one exists."""
        async with self._lock:
            return self._by_thread.get(thread_key)

    async def update_activity(self, turn_id: str, payload: dict) -> None:
        """Replace the current user-facing activity for one active turn."""
        activity = AssistantActivity.model_validate(payload)
        async with self._lock:
            managed = self._by_id.get(turn_id)
            if managed is not None:
                managed.activity = activity

    async def detach(self, turn_id: str, queue: asyncio.Queue[str | None]) -> None:
        """Detach one stream subscriber without canceling its assistant turn."""
        async with self._lock:
            managed = self._by_id.get(turn_id)
            if managed is not None:
                managed.subscribers.discard(queue)

    async def cancel(self, *, turn_id: str, thread_key: str) -> bool:
        """Cancel an active turn only when it belongs to the expected thread."""
        async with self._lock:
            managed = self._by_id.get(turn_id)
            if managed is None or managed.thread_key != thread_key or managed.task is None:
                return False
            task = managed.task
        task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancel and await all managed turns during application shutdown."""
        async with self._lock:
            tasks = [managed.task for managed in self._by_id.values() if managed.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


assistant_turn_manager = AssistantTurnManager()
