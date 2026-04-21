"""
Validator endpoints for Vocence Service.

Provides endpoints for validator-scoring coordination data.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from vocence.domain.config import ACTIVE_VALIDATOR_WINDOW_HOURS
from vocence.gateway.http.service.auth.signature import verify_validator_signature
from vocence.gateway.http.service.models import ActiveValidatorsResponse
from vocence.registry.persistence.repositories.validator_repository import ValidatorRepository


router = APIRouter()
validator_repo = ValidatorRepository()


@router.get("/active", response_model=ActiveValidatorsResponse)
async def get_active_validators(
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> ActiveValidatorsResponse:
    """Get validator hotkeys that submitted evaluations in the recent window."""
    active_hotkeys = await validator_repo.fetch_active_validator_hotkeys(
        threshold_hours=ACTIVE_VALIDATOR_WINDOW_HOURS
    )
    return ActiveValidatorsResponse(
        validators=active_hotkeys,
        count=len(active_hotkeys),
        threshold_hours=ACTIVE_VALIDATOR_WINDOW_HOURS,
    )
