"""Repository for persisted global scoring snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import GlobalScoringSnapshot


class GlobalScoringSnapshotRepository:
    """Store and fetch the latest dashboard-facing global scoring snapshot."""

    async def fetch_latest(self) -> Optional[GlobalScoringSnapshot]:
        async with acquire_session() as session:
            query = (
                select(GlobalScoringSnapshot)
                .where(GlobalScoringSnapshot.is_latest.is_(True))
                .order_by(GlobalScoringSnapshot.generated_at.desc(), GlobalScoringSnapshot.id.desc())
            )
            result = await session.execute(query)
            return result.scalars().first()

    async def upsert_latest(self, snapshot: dict[str, Any]) -> GlobalScoringSnapshot:
        """Insert a new snapshot when data changes, otherwise refresh the latest row timestamp."""
        payload = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        winner_hotkey = (snapshot.get("winner") or {}).get("hotkey")
        now = datetime.now(timezone.utc)

        async with acquire_session() as session:
            query = (
                select(GlobalScoringSnapshot)
                .where(GlobalScoringSnapshot.is_latest.is_(True))
                .order_by(GlobalScoringSnapshot.generated_at.desc(), GlobalScoringSnapshot.id.desc())
            )
            result = await session.execute(query)
            existing = result.scalars().first()

            if existing and existing.snapshot_hash == payload_hash:
                existing.snapshot_data = payload
                existing.winner_hotkey = winner_hotkey
                existing.generated_at = now
                existing.updated_at = now
                await session.flush()
                return existing

            await session.execute(
                update(GlobalScoringSnapshot)
                .where(GlobalScoringSnapshot.is_latest.is_(True))
                .values(is_latest=False)
            )

            row = GlobalScoringSnapshot(
                snapshot_hash=payload_hash,
                winner_hotkey=winner_hotkey,
                is_latest=True,
                snapshot_data=payload,
                generated_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            return row
