"""In-process background jobs for the NeuroCade monolith.

This package replaces the former Celery worker + Redis broker. Long-running work
(FastSurfer, workspace batches) is dispatched to a small pool of worker threads
inside the API process instead of a separate ``api-worker`` service. The public
surface is the :data:`job_manager` singleton; tasks register themselves with it
via :meth:`JobManager.task`.
"""

from .manager import JobManager, JobState, job_manager

__all__ = ["JobManager", "JobState", "job_manager"]
