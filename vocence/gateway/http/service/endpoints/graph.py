"""Graph activity endpoints for live subnet map."""

from typing import Annotated

from fastapi import APIRouter, Depends

from vocence.gateway.http.service.auth.signature import verify_validator_signature
from vocence.gateway.http.service.models import (
    WeightSettingFinishedRequest,
    WeightSettingStartedRequest,
)
from vocence.registry.persistence.repositories.graph_activity_repository import GraphActivityRepository


router = APIRouter()
graph_repo = GraphActivityRepository()


@router.post("/weights/start")
async def graph_weight_setting_started(
    body: WeightSettingStartedRequest,
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> dict:
    """Start or refresh a weight-setting lease for the live subnet graph."""
    await graph_repo.upsert_lease(
        activity_type="weight_setting",
        activity_key=f"weight:{hotkey}:{body.cycle_block}",
        validator_hotkey=hotkey,
        payload={
            "cycle_block": body.cycle_block,
            "phase": body.phase,
            "target_validator_hotkeys": list(body.target_validator_hotkeys or []),
        },
        ttl_seconds=120,
        status="active",
    )
    return {"ok": True}


@router.post("/weights/end")
async def graph_weight_setting_finished(
    body: WeightSettingFinishedRequest,
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> dict:
    """Finish a weight-setting lease and leave a short-lived completion pulse."""
    activity_key = f"weight:{hotkey}:{body.cycle_block}"
    await graph_repo.delete_lease(activity_key)
    await graph_repo.upsert_lease(
        activity_type="weight_setting_complete",
        activity_key=f"weight-complete:{hotkey}:{body.cycle_block}",
        validator_hotkey=hotkey,
        payload={
            "cycle_block": body.cycle_block,
            "result": body.result,
            "winner_hotkey": body.winner_hotkey,
        },
        ttl_seconds=15,
        status="complete",
    )
    return {"ok": True}
