"""In-process background jobs for the NeuroCade monolith.

Long-running neuroimaging workflows are dispatched to a small pool of worker
threads inside the API process through the :data:`job_manager` singleton.
"""

from .manager import job_manager

__all__ = ["job_manager"]
