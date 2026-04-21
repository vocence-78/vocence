"""
Status check endpoint for Vocence Service.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from sqlalchemy import text

from vocence.gateway.http.service.models import ServiceStatusResponse
from vocence import __version__


router = APIRouter()

# Track last metagraph sync
_last_metagraph_sync: Optional[datetime] = None


def record_last_sync(sync_time: datetime) -> None:
    """Record the last metagraph sync timestamp."""
    global _last_metagraph_sync
    _last_metagraph_sync = sync_time


@router.get("/health", response_model=ServiceStatusResponse)
async def check_status() -> ServiceStatusResponse:
    """Check service health status.
    
    Returns:
        Health status including database and metagraph sync state
    """
    # Check database connectivity
    db_healthy = False
    try:
        from vocence.registry.persistence.connection import acquire_session
        async with acquire_session() as session:
            await session.execute(text("SELECT 1"))
        db_healthy = True
    except Exception:
        pass
    
    return ServiceStatusResponse(
        status="healthy" if db_healthy else "degraded",
        version=__version__,
        database=db_healthy,
        metagraph_synced=_last_metagraph_sync is not None,
        last_sync=_last_metagraph_sync,
    )

