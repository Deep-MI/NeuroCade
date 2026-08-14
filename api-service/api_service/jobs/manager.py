"""In-process background job management for the NeuroCade monolith.

Jobs run on per-queue thread pools so the HTTP API stays responsive during the
hour-long neuroimaging runs. Concurrency is bounded per queue
(the GPU-bound ``fastsurfer`` queue defaults to a single worker to avoid GPU
contention). Cancellation cooperates with the runtime execution layer: when a
job launches a tool subprocess it registers the live process via
``neurocade_runtime_tools.execution.process_observer`` so the manager can
terminate the whole process group.

The manager keeps live process handles in memory and optionally persists job
submissions in SQLite. Queued durable jobs are restored on startup. Work that
was already running is recorded as interrupted because arbitrary container
processes cannot be resumed safely after process death.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from neurocade_runtime_tools.execution import _terminate_process_group, process_observer

from api_service.jobs.store import DurableJobStore

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "api"
FASTSURFER_QUEUE = "fastsurfer"


class JobState(str, Enum):
    """Lifecycle of an in-process job."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


_TERMINAL = frozenset({JobState.completed, JobState.failed, JobState.canceled})


@dataclass
class JobHandle:
    """Mutable state tracked for one submitted job."""

    id: str
    queue: str
    state: JobState = JobState.queued
    result: Any = None
    error: str | None = None
    future: Future | None = None
    finished_at: float | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    shutdown_requested: threading.Event = field(default_factory=threading.Event)
    _process: subprocess.Popen | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)


def _int_env(name: str, default: int) -> int:
    return max(1, int(os.environ.get(name, str(default)) or default))


def _default_concurrency() -> dict[str, int]:
    api = _int_env("API_WORKER_CONCURRENCY", 2)
    fastsurfer = _int_env("FASTSURFER_CONCURRENCY", 1)
    return {DEFAULT_QUEUE: api, FASTSURFER_QUEUE: fastsurfer}


class JobManager:
    """Register task functions and run submitted jobs on per-queue thread pools."""

    def __init__(
        self,
        concurrency: dict[str, int] | None = None,
        *,
        result_ttl_s: int | None = None,
        durable_store: DurableJobStore | None = None,
    ) -> None:
        self._tasks: dict[str, Callable[..., Any]] = {}
        self._handles: dict[str, JobHandle] = {}
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()
        self._concurrency = concurrency or _default_concurrency()
        self._default_concurrency = _int_env("API_WORKER_CONCURRENCY", 2)
        self._durable_store = durable_store
        # Terminal handles are kept only long enough for clients to poll the
        # result; durable run state lives in the ``runs`` table. Set to 0 to
        # disable eviction.
        self._result_ttl_s = (
            result_ttl_s if result_ttl_s is not None else max(0, int(os.environ.get("JOB_RESULT_TTL_SECONDS", "3600") or 0))
        )

    # -- registration ------------------------------------------------------
    def register(self, name: str, func: Callable[..., Any]) -> None:
        """Register a callable under a job name."""
        self._tasks[name] = func

    def configure_persistence(self, durable_store: DurableJobStore | None) -> None:
        """Attach or detach durable storage after database migrations complete."""
        with self._lock:
            self._durable_store = durable_store

    # -- submission --------------------------------------------------------
    def _executor_for(self, queue: str) -> ThreadPoolExecutor:
        with self._lock:
            executor = self._executors.get(queue)
            if executor is None:
                workers = self._concurrency.get(queue, self._default_concurrency)
                executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"job-{queue}")
                self._executors[queue] = executor
            return executor

    def _prune_terminal_locked(self) -> None:
        """Drop terminal job handles older than the result TTL. Caller holds ``_lock``."""
        if self._result_ttl_s <= 0:
            return
        cutoff = time.monotonic() - self._result_ttl_s
        expired = [
            job_id
            for job_id, handle in self._handles.items()
            if handle.state in _TERMINAL and handle.finished_at is not None and handle.finished_at < cutoff
        ]
        for job_id in expired:
            del self._handles[job_id]
        if expired:
            logger.info("job.evicted count=%d", len(expired))

    def submit(
        self,
        name: str,
        kwargs: dict[str, Any] | None = None,
        *,
        queue: str = DEFAULT_QUEUE,
        job_id: str | None = None,
    ) -> str:
        """Enqueue a registered task and return its job id."""
        func = self._tasks.get(name)
        if func is None:
            raise KeyError(f"Unknown job task: {name!r}")
        job_id = job_id or str(uuid.uuid4())
        handle = JobHandle(id=job_id, queue=queue)
        durable_store = self._durable_store
        if durable_store is not None:
            durable_store.create(
                job_id=job_id,
                task_name=name,
                queue_name=queue,
                kwargs=dict(kwargs or {}),
            )
        with self._lock:
            self._prune_terminal_locked()
            self._handles[job_id] = handle
        logger.info("job.submit id=%s task=%s queue=%s", job_id, name, queue)
        self._schedule(handle, name, func, dict(kwargs or {}))
        return job_id

    def _schedule(self, handle: JobHandle, name: str, func: Callable[..., Any], kwargs: dict[str, Any]) -> None:
        try:
            handle.future = self._executor_for(handle.queue).submit(self._run, handle, name, func, kwargs)
        except Exception as exc:
            durable_store = self._durable_store
            if durable_store is not None:
                durable_store.mark_terminal(handle.id, state=JobState.failed.value, error=str(exc))
            raise

    def _run(self, handle: JobHandle, name: str, func: Callable[..., Any], kwargs: dict[str, Any]) -> None:
        if handle.cancel_requested.is_set():
            handle.state = JobState.canceled
            handle.finished_at = time.monotonic()
            logger.info("job.canceled_before_start id=%s task=%s", handle.id, name)
            return
        durable_store = self._durable_store
        if durable_store is not None and not durable_store.mark_running(handle.id):
            handle.state = JobState.canceled
            handle.finished_at = time.monotonic()
            logger.info("job.skipped_nonqueued id=%s task=%s", handle.id, name)
            return
        handle.state = JobState.running

        def observe(process: subprocess.Popen) -> None:
            with handle._lock:
                handle._process = process
            if handle.cancel_requested.is_set():
                _terminate_process_group(process)

        token = process_observer.set(observe)
        try:
            handle.result = func(**kwargs)
            handle.state = JobState.canceled if handle.cancel_requested.is_set() else JobState.completed
            logger.info("job.completed id=%s task=%s state=%s", handle.id, name, handle.state.value)
        except Exception as exc:  # noqa: BLE001 - record failure, keep worker alive
            handle.error = str(exc)
            handle.state = JobState.canceled if handle.cancel_requested.is_set() else JobState.failed
            logger.exception("job.failed id=%s task=%s", handle.id, name)
        finally:
            handle.finished_at = time.monotonic()
            process_observer.reset(token)
            with handle._lock:
                handle._process = None
            if durable_store is not None and not handle.shutdown_requested.is_set():
                durable_store.mark_terminal(
                    handle.id,
                    state=handle.state.value,
                    result=handle.result,
                    error=handle.error,
                )

    # -- introspection -----------------------------------------------------
    def status(self, job_id: str) -> dict[str, Any]:
        """Return the readiness and result of a job."""
        handle = self._handles.get(job_id)
        if handle is None:
            durable_store = self._durable_store
            if durable_store is not None:
                persisted = durable_store.status(job_id)
                if persisted is not None:
                    return persisted
            return {"job_id": job_id, "status": "unknown", "ready": False, "result": None}
        ready = handle.state in _TERMINAL
        return {
            "job_id": job_id,
            "status": handle.state.value,
            "ready": ready,
            "result": handle.result if ready else None,
            "error": handle.error,
        }

    def queue_status(self, queue_names: set[str] | None = None) -> dict[str, int]:
        """Return active and queued counts, optionally filtered by queue name."""
        durable_store = self._durable_store
        if durable_store is not None:
            return durable_store.queue_status(queue_names)
        with self._lock:
            handles = list(self._handles.values())
        active = queued = 0
        for handle in handles:
            if queue_names is not None and handle.queue not in queue_names:
                continue
            if handle.state is JobState.running:
                active += 1
            elif handle.state is JobState.queued:
                queued += 1
        return {"active": active, "queued": queued, "total": active + queued}

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a job; terminate its tool process if running."""
        handle = self._handles.get(job_id)
        if handle is None:
            durable_store = self._durable_store
            if durable_store is not None:
                return durable_store.cancel(job_id)
            return False
        self._request_cancel(handle)
        durable_store = self._durable_store
        if durable_store is not None:
            durable_store.cancel(job_id)
        logger.info("job.cancel id=%s", job_id)
        return True

    def _request_cancel(self, handle: JobHandle, *, mark_terminal: bool = False, shutdown: bool = False) -> None:
        """Request cancellation and terminate any registered runtime subprocess."""
        handle.cancel_requested.set()
        if shutdown:
            handle.shutdown_requested.set()
        canceled_before_start = False
        if handle.future is not None:
            canceled_before_start = handle.future.cancel()  # false if already started

        with handle._lock:
            process = handle._process
        if process is not None:
            _terminate_process_group(process)

        if canceled_before_start or (mark_terminal and handle.state not in _TERMINAL):
            handle.state = JobState.canceled
            handle.finished_at = time.monotonic()

    def recover_pending(self, *, retention_days: int = 30) -> set[str]:
        """Restore queued jobs and fail jobs interrupted while already running."""
        durable_store = self._durable_store
        if durable_store is None:
            return set()
        recovered_ids: set[str] = set()
        for stored_job in durable_store.active_jobs():
            if stored_job.state == JobState.running.value:
                durable_store.mark_terminal(
                    stored_job.id,
                    state=JobState.failed.value,
                    error="Interrupted by an application restart.",
                )
                logger.warning("job.interrupted id=%s task=%s", stored_job.id, stored_job.task_name)
                continue
            func = self._tasks.get(stored_job.task_name)
            if func is None:
                durable_store.mark_terminal(
                    stored_job.id,
                    state=JobState.failed.value,
                    error=f"Registered task is unavailable: {stored_job.task_name}",
                )
                logger.error("job.recovery_unknown_task id=%s task=%s", stored_job.id, stored_job.task_name)
                continue
            handle = JobHandle(id=stored_job.id, queue=stored_job.queue_name)
            with self._lock:
                self._handles[handle.id] = handle
            self._schedule(handle, stored_job.task_name, func, stored_job.kwargs)
            recovered_ids.add(handle.id)
            logger.info("job.recovered id=%s task=%s queue=%s", handle.id, stored_job.task_name, handle.queue)
        pruned = durable_store.prune_terminal(retention_days=retention_days)
        if pruned:
            logger.info("job.history_pruned count=%d", pruned)
        return recovered_ids

    def shutdown(self, *, wait: bool = False) -> None:
        """Stop all worker pools (used on application shutdown)."""
        with self._lock:
            handles = list(self._handles.values())
            executors = list(self._executors.values())
            self._executors.clear()
        for handle in handles:
            if handle.state not in _TERMINAL:
                self._request_cancel(handle, mark_terminal=True, shutdown=True)
        for executor in executors:
            executor.shutdown(wait=wait, cancel_futures=True)


job_manager = JobManager()
