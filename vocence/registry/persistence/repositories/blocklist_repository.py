"""
Blocklist Repository.

Handles blocked participant management.
"""

from typing import List, Optional

from sqlalchemy import select, delete

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import BlockedEntity
from vocence.shared.logging import emit_log


class BlocklistRepository:
    """Repository for blocked_entities table (participant hotkeys only)."""
    
    async def add_entry(
        self,
        hotkey: str,
        reason: Optional[str] = None,
        added_by: Optional[str] = None,
    ) -> BlockedEntity:
        """Add a participant to the blocklist.
        
        Args:
            hotkey: Participant's hotkey
            reason: Reason for blocking
            added_by: Admin hotkey who added
            
        Returns:
            BlockedEntity entry
        """
        async with acquire_session() as session:
            # Check if already blocked
            query = select(BlockedEntity).where(BlockedEntity.hotkey == hotkey)
            result = await session.execute(query)
            existing = result.scalar_one_or_none()
            
            if existing:
                existing.reason = reason
                existing.added_by = added_by
                entry = existing
            else:
                entry = BlockedEntity(
                    hotkey=hotkey,
                    reason=reason,
                    added_by=added_by,
                )
                session.add(entry)
            
            await session.flush()
            emit_log(f"Added participant {hotkey[:8]}... to blocklist: {reason}", "info")
            return entry
    
    async def remove_entry(self, hotkey: str) -> bool:
        """Remove a participant from the blocklist.
        
        Args:
            hotkey: Participant's hotkey
            
        Returns:
            True if removed, False if not found
        """
        async with acquire_session() as session:
            stmt = delete(BlockedEntity).where(BlockedEntity.hotkey == hotkey)
            result = await session.execute(stmt)
            if result.rowcount > 0:
                emit_log(f"Removed participant {hotkey[:8]}... from blocklist", "info")
                return True
            return False
    
    async def is_blocked(self, hotkey: str) -> bool:
        """Check if a participant is blocked.
        
        Args:
            hotkey: Participant's hotkey
            
        Returns:
            True if blocked
        """
        async with acquire_session() as session:
            query = select(BlockedEntity).where(BlockedEntity.hotkey == hotkey)
            result = await session.execute(query)
            return result.scalar_one_or_none() is not None
    
    async def fetch_entry(self, hotkey: str) -> Optional[BlockedEntity]:
        """Get blocklist entry for a participant."""
        async with acquire_session() as session:
            query = select(BlockedEntity).where(BlockedEntity.hotkey == hotkey)
            result = await session.execute(query)
            return result.scalar_one_or_none()
    
    async def fetch_all(self) -> List[BlockedEntity]:
        """Get all blocked participants.
        
        Returns:
            List of BlockedEntity entries
        """
        async with acquire_session() as session:
            query = select(BlockedEntity)
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def fetch_blocked_hotkeys(self) -> List[str]:
        """Get list of all blocked participant hotkeys."""
        entries = await self.fetch_all()
        return [e.hotkey for e in entries]

