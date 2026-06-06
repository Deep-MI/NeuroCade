"""Provide API service celery app behavior for NeuroCade."""

from celery import Celery

from backend_common.settings import get_settings


settings = get_settings()


celery_app = Celery("api_service", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.task_default_queue = "api"
celery_app.conf.imports = (
    "api_service.workspace_batch.tasks",
    "api_service.runtime.fastsurfer_tasks",
)
