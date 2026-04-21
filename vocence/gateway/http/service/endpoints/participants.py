"""
Participants endpoints for Vocence Service.

Provides endpoints for getting valid participants list and participant details.
"""

from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException

from vocence.gateway.http.service.auth.signature import verify_validator_signature
from vocence.gateway.http.service.models import ParticipantResponse, ParticipantsListResponse
from vocence.registry.persistence.repositories.miner_repository import MinerRepository


router = APIRouter()
participant_repo = MinerRepository()


@router.get("/valid", response_model=ParticipantsListResponse)
async def get_valid_participants(
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> ParticipantsListResponse:
    """Get list of valid participants.
    
    Requires validator signature authentication.
    
    Returns:
        List of valid participants with their details
    """
    participants = await participant_repo.fetch_valid_miners()
    all_participants = await participant_repo.fetch_all_miners()
    
    return ParticipantsListResponse(
        participants=[
            ParticipantResponse(
                uid=p.uid,
                hotkey=p.miner_hotkey,
                model_name=p.model_name,
                model_revision=p.model_revision,
                model_hash=p.model_hash,
                chute_id=p.chute_id,
                chute_slug=p.chute_slug,
                is_valid=p.is_valid,
                invalid_reason=p.invalid_reason,
                block=p.block,
                last_validated_at=p.last_validated_at,
            )
            for p in participants
        ],
        total=len(all_participants),
        valid_count=len(participants),
    )


@router.get("/all", response_model=ParticipantsListResponse)
async def get_all_participants(
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> ParticipantsListResponse:
    """Get all participants (valid and invalid).
    
    Requires validator signature authentication.
    
    Returns:
        List of all participants with their details
    """
    participants = await participant_repo.fetch_all_miners()
    valid_count = await participant_repo.count_valid()
    
    return ParticipantsListResponse(
        participants=[
            ParticipantResponse(
                uid=p.uid,
                hotkey=p.miner_hotkey,
                model_name=p.model_name,
                model_revision=p.model_revision,
                model_hash=p.model_hash,
                chute_id=p.chute_id,
                chute_slug=p.chute_slug,
                is_valid=p.is_valid,
                invalid_reason=p.invalid_reason,
                block=p.block,
                last_validated_at=p.last_validated_at,
            )
            for p in participants
        ],
        total=len(participants),
        valid_count=valid_count,
    )


@router.get("/{participant_hotkey}", response_model=ParticipantResponse)
async def get_participant(
    participant_hotkey: str,
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> ParticipantResponse:
    """Get details for a specific participant.
    
    Args:
        participant_hotkey: Participant's SS58 hotkey
        
    Requires validator signature authentication.
    
    Returns:
        Participant details
        
    Raises:
        HTTPException: If participant not found
    """
    participant = await participant_repo.fetch_by_hotkey(participant_hotkey)
    
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")
    
    return ParticipantResponse(
        uid=participant.uid,
        hotkey=participant.miner_hotkey,
        model_name=participant.model_name,
        model_revision=participant.model_revision,
        model_hash=participant.model_hash,
        chute_id=participant.chute_id,
        chute_slug=participant.chute_slug,
        is_valid=participant.is_valid,
        invalid_reason=participant.invalid_reason,
        block=participant.block,
        last_validated_at=participant.last_validated_at,
    )

