"""
Validator Registry Repository.

Handles registered validator tracking.
"""

from typing import List, Optional
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update, func

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import ValidatorRegistry, ValidatorEvaluation
from vocence.shared.logging import emit_log


class ValidatorRepository:
    """Repository for validator_registry table."""
    
    async def upsert_validator(
        self,
        uid: int,
        hotkey: str,
        stake: float = 0.0,
        s3_bucket: Optional[str] = None,
    ) -> ValidatorRegistry:
        """Save or update a validator record.
        
        Args:
            uid: Validator UID
            hotkey: Validator's SS58 hotkey
            stake: Validator's stake
            s3_bucket: Optional S3 bucket for samples
            
        Returns:
            ValidatorRegistry instance
        """
        async with acquire_session() as session:
            existing = await session.get(ValidatorRegistry, uid)
            
            if existing:
                existing.hotkey = hotkey
                existing.stake = stake
                if s3_bucket:
                    existing.s3_bucket = s3_bucket
                existing.last_seen_at = datetime.now(timezone.utc)
                validator = existing
            else:
                validator = ValidatorRegistry(
                    uid=uid,
                    hotkey=hotkey,
                    stake=stake,
                    s3_bucket=s3_bucket,
                    last_seen_at=datetime.now(timezone.utc),
                )
                session.add(validator)
            
            await session.flush()
            return validator
    
    async def fetch_by_uid(self, uid: int) -> Optional[ValidatorRegistry]:
        """Get validator by UID."""
        async with acquire_session() as session:
            return await session.get(ValidatorRegistry, uid)
    
    async def fetch_by_hotkey(self, hotkey: str) -> Optional[ValidatorRegistry]:
        """Get validator by hotkey."""
        async with acquire_session() as session:
            query = select(ValidatorRegistry).where(ValidatorRegistry.hotkey == hotkey)
            result = await session.execute(query)
            return result.scalar_one_or_none()
    
    async def fetch_all_validators(self) -> List[ValidatorRegistry]:
        """Get all validators."""
        async with acquire_session() as session:
            query = select(ValidatorRegistry).order_by(ValidatorRegistry.uid)
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def update_last_seen(self, hotkey: str) -> bool:
        """Update validator's last seen timestamp.
        
        Args:
            hotkey: Validator's hotkey
            
        Returns:
            True if updated, False if not found
        """
        async with acquire_session() as session:
            stmt = (
                update(ValidatorRegistry)
                .where(ValidatorRegistry.hotkey == hotkey)
                .values(last_seen_at=datetime.now(timezone.utc))
            )
            result = await session.execute(stmt)
            return result.rowcount > 0
    
    async def update_stake(self, hotkey: str, stake: float) -> bool:
        """Update validator's stake.
        
        Args:
            hotkey: Validator's hotkey
            stake: New stake value
            
        Returns:
            True if updated, False if not found
        """
        async with acquire_session() as session:
            stmt = (
                update(ValidatorRegistry)
                .where(ValidatorRegistry.hotkey == hotkey)
                .values(stake=stake)
            )
            result = await session.execute(stmt)
            return result.rowcount > 0
    
    async def count_validators(self) -> int:
        """Get total validator count."""
        async with acquire_session() as session:
            result = await session.execute(select(func.count(ValidatorRegistry.uid)))
            return result.scalar_one()
    
    async def fetch_by_stake(self, min_stake: float = 0.0) -> List[ValidatorRegistry]:
        """Get validators with stake above minimum.
        
        Args:
            min_stake: Minimum stake threshold
            
        Returns:
            List of validators
        """
        async with acquire_session() as session:
            query = (
                select(ValidatorRegistry)
                .where(ValidatorRegistry.stake >= min_stake)
                .order_by(ValidatorRegistry.stake.desc())
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def fetch_active_validator_hotkeys(self, threshold_hours: int = 24) -> List[str]:
        """Return validator hotkeys with recent evaluation submissions."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)
        async with acquire_session() as session:
            query = (
                select(ValidatorEvaluation.validator_hotkey)
                .group_by(ValidatorEvaluation.validator_hotkey)
                .having(func.max(ValidatorEvaluation.evaluated_at) >= cutoff)
                .order_by(ValidatorEvaluation.validator_hotkey)
            )
            result = await session.execute(query)
            return [row[0] for row in result.all() if row[0]]
