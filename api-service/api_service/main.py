"""Provide API service main behavior for NeuroCade."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api_service.bootstrap import bootstrap_database, seed_demo_state
from api_service.middleware import register_app_middleware
from api_service.routers import app_runtime, artifacts, assistant, assistant_turns, auth, cases, monitoring, providers, workspaces
from api_service.runtime import logger
from backend_common.auth import allow_local_auth, validate_auth_configuration
from backend_common.db import SessionLocal, engine


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate startup configuration and prepare the application database."""
    validate_auth_configuration()
    bootstrap_database(engine)
    with SessionLocal() as startup_db:
        seed_demo_state(startup_db)
    if allow_local_auth():
        logger.warning("Local auth fallback is enabled; do not use this configuration outside local deployments.")
    yield


app = FastAPI(
    title="NeuroCade App API",
    openapi_url="/api/app/openapi.json",
    docs_url="/api/app/docs",
    redoc_url=None,
    lifespan=lifespan,
)


register_app_middleware(app)



@app.get("/healthz")
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
