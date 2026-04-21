"""Repository for live graph activity leases."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from sqlalchemy import delete, select

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import GraphActivityLease


class GraphActivityRepository:
    """Store and read short-lived graph activity leases."""

    async def upsert_lease(
        self,
        activity_type: str,
        activity_key: str,
        validator_hotkey: str,
        payload: Optional[dict[str, Any]] = None,
        ttl_seconds: int = 120,
        status: str = "active",
    ) -> GraphActivityLease:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(1, ttl_seconds))
        payload_json = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True)

        async with acquire_session() as session:
            query = select(GraphActivityLease).where(GraphActivityLease.activity_key == activity_key)
            result = await session.execute(query)
            existing = result.scalar_one_or_none()
            if existing:
                existing.activity_type = activity_type
                existing.validator_hotkey = validator_hotkey
                existing.status = status
                existing.payload_json = payload_json
                existing.expires_at = expires_at
                existing.updated_at = now
                await session.flush()
                return existing

            row = GraphActivityLease(
                activity_type=activity_type,
                activity_key=activity_key,
                validator_hotkey=validator_hotkey,
                status=status,
                payload_json=payload_json,
                started_at=now,
                expires_at=expires_at,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            return row

    async def delete_lease(self, activity_key: str) -> int:
        async with acquire_session() as session:
            result = await session.execute(
                delete(GraphActivityLease).where(GraphActivityLease.activity_key == activity_key)
            )
            await session.flush()
            return result.rowcount or 0

    async def fetch_current(self) -> List[GraphActivityLease]:
        now = datetime.now(timezone.utc)
        async with acquire_session() as session:
            query = (
                select(GraphActivityLease)
                .where(GraphActivityLease.expires_at >= now)
                .order_by(GraphActivityLease.started_at.desc(), GraphActivityLease.id.desc())
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def prune_expired(self) -> int:
        now = datetime.now(timezone.utc)
        async with acquire_session() as session:
            result = await session.execute(
                delete(GraphActivityLease).where(GraphActivityLease.expires_at < now)
            )
            await session.flush()
            return result.rowcount or 0
