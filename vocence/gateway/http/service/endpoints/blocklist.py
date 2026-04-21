"""
Blocklist endpoints for Vocence Service.

Provides endpoints for managing blocked participants.
"""

from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException

from vocence.gateway.http.service.auth.signature import verify_admin_signature
from vocence.gateway.http.service.models import BlocklistEntry, BlocklistResponse
from vocence.registry.persistence.repositories.blocklist_repository import BlocklistRepository


router = APIRouter()
blocklist_repo = BlocklistRepository()


@router.get("/participants", response_model=List[str])
async def get_blocked_participants() -> List[str]:
    """Get list of blocked participant hotkeys.
    
    This endpoint is public (no authentication required).
    
    Returns:
        List of blocked participant hotkeys
    """
    return await blocklist_repo.fetch_blocked_hotkeys()


@router.post("", response_model=BlocklistResponse)
async def add_to_blocklist(
    entry: BlocklistEntry,
    admin_hotkey: Annotated[str, Depends(verify_admin_signature)],
) -> BlocklistResponse:
    """Add a participant to the blocklist.
    
    Requires admin signature authentication.
    
    Args:
        entry: Blocklist entry data
        
    Returns:
        Created blocklist entry
    """
    result = await blocklist_repo.add_entry(
        hotkey=entry.hotkey,
        reason=entry.reason,
        added_by=admin_hotkey,
    )
    
    return BlocklistResponse(
        hotkey=result.hotkey,
        reason=result.reason,
        added_by=result.added_by,
        created_at=result.created_at,
    )


@router.delete("/{hotkey}")
async def remove_from_blocklist(
    hotkey: str,
    admin_hotkey: Annotated[str, Depends(verify_admin_signature)],
) -> dict:
    """Remove a participant from the blocklist.
    
    Requires admin signature authentication.
    
    Args:
        hotkey: Participant's SS58 hotkey
        
    Returns:
        Confirmation message
        
    Raises:
        HTTPException: If participant not found
    """
    removed = await blocklist_repo.remove_entry(hotkey)
    
    if not removed:
        raise HTTPException(status_code=404, detail="Participant not blocked")
    
    return {
        "success": True,
        "message": f"Removed participant {hotkey[:8]}... from blocklist",
        "removed_by": admin_hotkey,
    }

