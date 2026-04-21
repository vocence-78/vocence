"""
FastAPI application for Vocence Service.

Provides centralized service for validators to:
- Get list of valid participants
- Submit evaluation metadata
- Manage blocked entities
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from vocence import __version__
from vocence.shared.logging import emit_log, print_header
from vocence.registry.persistence.connection import establish_connection, terminate_connection
from vocence.gateway.http.service.endpoints.participants import router as participants_router
from vocence.gateway.http.service.endpoints.evaluations import router as evaluations_router
from vocence.gateway.http.service.endpoints.blocklist import router as blocklist_router
from vocence.gateway.http.service.endpoints.validators import router as validators_router
from vocence.gateway.http.service.endpoints.graph import router as graph_router
from vocence.gateway.http.service.endpoints.status import router as status_router
from vocence.gateway.http.service.tasks import (
    ParticipantValidationTask,
    MetricsCalculationTask,
)
from vocence.gateway.http.service.auth.rate_limit import HotkeyRateLimitMiddleware


# Global task references
_background_workers: list[asyncio.Task] = []


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager.
    
    Initializes database and starts background workers on startup.
    Cleans up resources on shutdown.
    """
    print_header("Vocence Service Starting")
    
    # Initialize database
    await establish_connection()
    emit_log("Database initialized", "success")
    
    # Create tables if needed
    from vocence.registry.persistence.connection import initialize_schema
    await initialize_schema()
    
    # Start background workers
    validation_worker = ParticipantValidationTask()
    metrics_worker = MetricsCalculationTask()
    
    _background_workers.append(asyncio.create_task(validation_worker.run()))
    _background_workers.append(asyncio.create_task(metrics_worker.run()))
    emit_log("Background workers started (validation, global_scoring)", "success")
    
    yield
    
    # Cleanup
    emit_log("Shutting down...", "info")
    
    # Cancel background workers
    for worker in _background_workers:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
    
    # Close database
    await terminate_connection()
    emit_log("Shutdown complete", "success")


# Create FastAPI application
app = FastAPI(
    title="Vocence Service",
    description="Centralized service for Vocence voice intelligence subnet validators",
    version=__version__,
    lifespan=application_lifespan,
)

# Configure CORS — restrict to vocence.ai by default; override via CORS_ORIGINS
# (comma-separated list) for staging/dev. Never use "*" in production because
# validator signing headers are credentials and the API accepts signed writes.
_DEFAULT_CORS_ORIGINS = [
    "https://vocence.ai",
    "https://www.vocence.ai",
    "https://backend.vocence.ai",
]
_cors_env = (os.environ.get("CORS_ORIGINS") or "").strip()
_allowed_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env
    else _DEFAULT_CORS_ORIGINS
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Accept",
        "Authorization",
        "X-Validator-Hotkey",
        "X-Signature",
        "X-Timestamp",
        "X-Nonce",
    ],
)

# TrustedHost: only accept requests whose Host header matches the expected
# production domains. Reverse-proxied traffic from nginx arrives with the
# original Host, so this catches direct hits bypassing nginx.
_trusted_hosts_env = (os.environ.get("TRUSTED_HOSTS") or "").strip()
_trusted_hosts = (
    [h.strip() for h in _trusted_hosts_env.split(",") if h.strip()]
    if _trusted_hosts_env
    else [
        "subnet.vocence.ai",
        "api.vocence.ai",
        "localhost",
        "127.0.0.1",
    ]
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

# Per-hotkey sliding-window rate limit on write endpoints (default 2 req / 10min).
# Tunable via RATE_LIMIT_MAX_REQUESTS and RATE_LIMIT_WINDOW_SECONDS env vars.
app.add_middleware(HotkeyRateLimitMiddleware)

# Register routers
app.include_router(status_router, tags=["Status"])
app.include_router(participants_router, prefix="/participants", tags=["Participants"])
app.include_router(evaluations_router, prefix="/evaluations", tags=["Evaluations"])
app.include_router(blocklist_router, prefix="/blocklist", tags=["Blocklist"])
app.include_router(validators_router, prefix="/validators", tags=["Validators"])
app.include_router(graph_router, prefix="/graph", tags=["Graph"])


def run_service() -> None:
    """Entry point for running the service."""
    import uvicorn
    from vocence.domain.config import SERVICE_HOST, SERVICE_PORT, SERVICE_RELOAD

    uvicorn.run(
        "vocence.gateway.http.service.app:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        reload=SERVICE_RELOAD,
    )


if __name__ == "__main__":
    run_service()
