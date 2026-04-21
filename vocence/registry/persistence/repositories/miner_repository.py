"""
Registered Miners Repository.

Handles centrally validated miner state.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from sqlalchemy import select, update, delete, func

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import RegisteredMiner
from vocence.shared.logging import emit_log


class MinerRepository:
    """Repository for registered_miners table."""
    
    async def upsert_miner(
        self,
        uid: int,
        miner_hotkey: str,
        block: Optional[int] = None,
        model_name: Optional[str] = None,
        model_revision: Optional[str] = None,
        model_hash: Optional[str] = None,
        chute_id: Optional[str] = None,
        chute_slug: Optional[str] = None,
        is_valid: bool = False,
        invalid_reason: Optional[str] = None,
    ) -> RegisteredMiner:
        """Save or update a registered miner record.
        
        Uses upsert logic - updates if exists, inserts if new.
        
        Args:
            uid: Miner UID (0-255)
            miner_hotkey: Miner's SS58 hotkey
            block: Block when miner committed
            model_name: HuggingFace model repository
            model_revision: Git commit hash for the model
            model_hash: Hash of model weights
            chute_id: Chutes deployment UUID
            chute_slug: Chute URL slug
            is_valid: Validation status
            invalid_reason: Reason if invalid
            
        Returns:
            RegisteredMiner instance
        """
        async with acquire_session() as session:
            existing = await session.get(RegisteredMiner, uid)
            
            if existing:
                existing.miner_hotkey = miner_hotkey
                existing.block = block
                existing.model_name = model_name
                existing.model_revision = model_revision
                existing.model_hash = model_hash
                existing.chute_id = chute_id
                existing.chute_slug = chute_slug
                existing.is_valid = is_valid
                existing.invalid_reason = invalid_reason
                existing.last_validated_at = datetime.now(timezone.utc)
                miner = existing
            else:
                miner = RegisteredMiner(
                    uid=uid,
                    miner_hotkey=miner_hotkey,
                    block=block,
                    model_name=model_name,
                    model_revision=model_revision,
                    model_hash=model_hash,
                    chute_id=chute_id,
                    chute_slug=chute_slug,
                    is_valid=is_valid,
                    invalid_reason=invalid_reason,
                    last_validated_at=datetime.now(timezone.utc),
                )
                session.add(miner)
            
            await session.flush()
            return miner
    
    async def fetch_by_uid(self, uid: int) -> Optional[RegisteredMiner]:
        """Get miner by UID."""
        async with acquire_session() as session:
            return await session.get(RegisteredMiner, uid)
    
    async def fetch_by_hotkey(self, hotkey: str) -> Optional[RegisteredMiner]:
        """Get miner by hotkey."""
        async with acquire_session() as session:
            query = select(RegisteredMiner).where(RegisteredMiner.miner_hotkey == hotkey)
            result = await session.execute(query)
            return result.scalar_one_or_none()
    
    async def fetch_valid_miners(self) -> List[RegisteredMiner]:
        """Get all valid miners."""
        async with acquire_session() as session:
            query = select(RegisteredMiner).where(RegisteredMiner.is_valid == True)
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def fetch_all_miners(self) -> List[RegisteredMiner]:
        """Get all miners."""
        async with acquire_session() as session:
            query = select(RegisteredMiner).order_by(RegisteredMiner.uid)
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def update_validation_status(
        self,
        uid: int,
        is_valid: bool,
        invalid_reason: Optional[str] = None,
    ) -> bool:
        """Set miner validation status."""
        async with acquire_session() as session:
            stmt = (
                update(RegisteredMiner)
                .where(RegisteredMiner.uid == uid)
                .values(
                    is_valid=is_valid,
                    invalid_reason=invalid_reason,
                    last_validated_at=datetime.now(timezone.utc),
                )
            )
            result = await session.execute(stmt)
            return result.rowcount > 0
    
    async def bulk_upsert_miners(
        self,
        miners: List[Dict[str, Any]],
    ) -> int:
        """Batch upsert miner records.
        
        Args:
            miners: List of miner dicts with keys matching RegisteredMiner fields
            
        Returns:
            Number of miners processed
        """
        count = 0
        for miner_data in miners:
            await self.upsert_miner(
                uid=miner_data["uid"],
                miner_hotkey=miner_data["miner_hotkey"],
                block=miner_data.get("block"),
                model_name=miner_data.get("model_name"),
                model_revision=miner_data.get("model_revision"),
                model_hash=miner_data.get("model_hash"),
                chute_id=miner_data.get("chute_id"),
                chute_slug=miner_data.get("chute_slug"),
                is_valid=miner_data.get("is_valid", False),
                invalid_reason=miner_data.get("invalid_reason"),
            )
            count += 1
        
        emit_log(f"Processed {count} miners", "info")
        return count
    
    async def remove_inactive_miners(self, active_uids: List[int]) -> int:
        """Delete miners not in the active UIDs list.
        
        Args:
            active_uids: List of currently active UIDs
            
        Returns:
            Number of deleted miners
        """
        # Guard against empty list which would delete ALL miners
        if not active_uids:
            emit_log("remove_inactive_miners called with empty active_uids list, skipping", "warn")
            return 0
        
        async with acquire_session() as session:
            stmt = delete(RegisteredMiner).where(RegisteredMiner.uid.not_in(active_uids))
            result = await session.execute(stmt)
            deleted = result.rowcount
            if deleted > 0:
                emit_log(f"Deleted {deleted} inactive miners", "info")
            return deleted
    
    async def count_valid(self) -> int:
        """Get count of valid miners."""
        async with acquire_session() as session:
            result = await session.execute(
                select(func.count(RegisteredMiner.uid)).where(RegisteredMiner.is_valid == True)
            )
            return result.scalar_one()
    
    async def count_total(self) -> int:
        """Get total miner count."""
        async with acquire_session() as session:
            result = await session.execute(select(func.count(RegisteredMiner.uid)))
            return result.scalar_one()

