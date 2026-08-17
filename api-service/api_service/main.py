"""Provide API service main behavior for NeuroCade."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api_service.assistant.tool_execution_store import reconcile_interrupted_tool_executions
from api_service.assistant.turn_store import reconcile_interrupted_turns
from api_service.bootstrap import bootstrap_database, seed_demo_state
from api_service.jobs import job_manager
from api_service.jobs.reconcile import reconcile_interrupted_runs
from api_service.jobs.store import DurableJobStore
from api_service.jobs.update_checker import start_update_checker
from api_service.middleware import register_app_middleware
from api_service.routers import app_runtime, artifacts, assistant, assistant_turns, auth, cases, monitoring, providers, workspaces
from api_service.runtime import logger
from api_service.runtime.neuroimaging_tasks import register_neuroimaging_tasks
from api_service.runtime_tools.workflow_catalog import load_workflow_catalog
from api_service.runtime_tools.workflow_execution import warm_workflow_gpu_capabilities
from api_service.runtime_tools.workflow_outputs import index_all_case_workflow_outputs
from backend_common.artifact_reconciliation import reconcile_all_artifacts
from backend_common.auth import allow_local_auth, validate_auth_configuration
from backend_common.db import SessionLocal, engine
from backend_common.settings import get_settings

startup_logger = logging.getLogger("uvicorn.error")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate startup configuration and prepare the application database.

    The monolith is designed to run as a SINGLE process. The in-process
    JobManager (and its job registry) and SQLite's single-writer model both
    assume one process: launching multiple uvicorn/gunicorn workers would make
    jobs invisible across workers and turn DB access into cross-process
    contention. Run ``uvicorn`` with the default single worker (no ``--workers``,
    no ``WEB_CONCURRENCY``); see ``docker/backend.Dockerfile``.
    """
    startup_logger.info("Starting NeuroCade backend (single-process model: one worker, in-process jobs + SQLite).")
    try:
        load_workflow_catalog()
        register_neuroimaging_tasks()
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            startup_logger.info("Warming analysis runtime capability checks.")
            warm_workflow_gpu_capabilities()
        startup_logger.info("Validating auth configuration.")
        validate_auth_configuration()
        startup_logger.info("Applying database migrations.")
        bootstrap_database(engine)
        startup_logger.info("Seeding local demo state.")
        with SessionLocal() as startup_db:
            seed_demo_state(startup_db)
        startup_logger.info("Recovering durable background jobs.")
        job_manager.configure_persistence(DurableJobStore(SessionLocal))
        recovered_job_ids = job_manager.recover_pending(
            retention_days=settings.job_history_retention_days,
        )
        startup_logger.info("Reconciling interrupted runs.")
        reconcile_interrupted_runs(
            SessionLocal,
            recovered_job_ids=recovered_job_ids,
        )
        with SessionLocal() as startup_db:
            tool_recoveries = reconcile_interrupted_tool_executions(startup_db)
            interrupted_turn_count = reconcile_interrupted_turns(
                startup_db,
                tool_recoveries=tool_recoveries,
            )
            if interrupted_turn_count:
                startup_logger.warning(
                    "Marked %s interrupted assistant turn(s) as failed.",
                    interrupted_turn_count,
                )
            index_all_case_workflow_outputs(startup_db, settings)
            reconcile_all_artifacts(startup_db)
            startup_db.commit()
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            startup_logger.info("Starting update checker.")
            start_update_checker()
        if allow_local_auth():
            logger.warning("Local auth fallback is enabled; do not use this configuration outside local deployments.")
        browser_url = (
            os.environ.get("NEUROCADE_ACCESS_URL")
            or settings.app_base_url
        )
        startup_logger.info(
            "Open NeuroCade in a browser at %s (0.0.0.0 is a server bind address, not a browser URL).",
            browser_url,
        )
        startup_logger.info("NeuroCade backend startup checks complete.")
    except Exception:
        job_manager.configure_persistence(None)
        startup_logger.exception("NeuroCade backend startup failed.")
        raise
    try:
        yield
    finally:
        job_manager.shutdown(wait=False)
        job_manager.configure_persistence(None)


app = FastAPI(
    title="NeuroCade App API",
    openapi_url="/api/app/openapi.json",
    docs_url="/api/app/docs",
    redoc_url=None,
    lifespan=lifespan,
)


register_app_middleware(app)



@app.get("/api/app/healthz")
def healthz() -> dict:
    """Return the API service health status."""
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(providers.router)
app.include_router(assistant.router)
app.include_router(assistant_turns.router)
app.include_router(artifacts.router)
app.include_router(cases.router)
app.include_router(monitoring.router)
app.include_router(app_runtime.router)


def _mount_client(application: FastAPI) -> None:
    """Serve the built SPA frontend from the application process.

    The client is served from a single origin (no CORS), with unknown non-API
    paths falling back to ``index.html`` for client-side routing. Mounted only
    when a build is present, so dev/test runs without a build still work.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from backend_common.settings import ROOT_DIR

    dist_dir = Path(ROOT_DIR) / "client" / "dist"
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        return

    class ImmutableStaticFiles(StaticFiles):
        async def get_response(self, path, scope):  # noqa: ANN001
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    application.mount("/assets", ImmutableStaticFiles(directory=dist_dir / "assets"), name="assets")

    def dist_file_for_path(path: str) -> Path | None:
        requested = (dist_dir / path.lstrip("/")).resolve()
        try:
            requested.relative_to(dist_dir.resolve())
        except ValueError:
            return None
        return requested if requested.is_file() else None

    @application.exception_handler(StarletteHTTPException)
    async def _spa_fallback(request, exc):  # noqa: ANN001
        # Serve the SPA shell for unmatched GET routes that are not API/health calls.
        if (
            exc.status_code == 404
            and request.method == "GET"
            and not request.url.path.startswith(("/api/", "/assets/"))
        ):
            dist_file = dist_file_for_path(request.url.path)
            if dist_file is not None:
                return FileResponse(dist_file, headers={"Cache-Control": "no-cache"})
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        # Match Starlette's default HTTP exception handler: preserve any headers
        # the exception carries (e.g. WWW-Authenticate on 401, Retry-After on 429).
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @application.get("/", include_in_schema=False)
    async def _serve_index() -> FileResponse:
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})


_mount_client(app)
