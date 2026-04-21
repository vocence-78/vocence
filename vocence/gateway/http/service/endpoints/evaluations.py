"""
Evaluations endpoints for Vocence Service.

Provides endpoints for submitting evaluation metadata.
"""

import json
from typing import Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from vocence.gateway.http.service.auth.signature import verify_validator_signature
from vocence.gateway.http.service.models import (
    EvaluationSubmission,
    EvaluationResponse,
    LiveEvaluationStartedRequest,
    LiveEvaluationCancelRequest,
)
from vocence.registry.persistence.repositories.evaluation_repository import EvaluationRepository
from vocence.shared.logging import emit_log


router = APIRouter()
evaluation_repo = EvaluationRepository()

MAX_BATCH_SIZE = 100  # Maximum evaluations allowed per batch request


def _decode_element_scores(raw: Optional[str]) -> Optional[Dict[str, float]]:
    """Decode JSON-encoded per-element scores stored on ValidatorEvaluation."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(k): float(v) for k, v in parsed.items() if isinstance(v, (int, float))}


@router.post("/live")
async def live_evaluation_started(
    body: LiveEvaluationStartedRequest,
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> dict:
    """Notify that an evaluation has started (prompt generated, miners about to be evaluated).

    Used by the dashboard validation status bar to show "pending". When the same
    validator submits results via POST /evaluations or POST /evaluations/batch
    for this evaluation_id, the pending row is removed.
    """
    await evaluation_repo.add_live_pending(
        validator_hotkey=hotkey,
        evaluation_id=body.evaluation_id,
        prompt_summary=body.prompt_summary,
        miner_hotkeys=body.miner_hotkeys or None,
    )
    await evaluation_repo.start_evaluation_graph_activity(
        validator_hotkey=hotkey,
        evaluation_id=body.evaluation_id,
        prompt_summary=body.prompt_summary,
        miner_hotkeys=body.miner_hotkeys or None,
    )
    emit_log(
        f"Live evaluation pending: validator={hotkey[:12]}..., eval_id={body.evaluation_id}, miners={len(body.miner_hotkeys or [])}",
        "info",
    )
    return {"ok": True}


@router.post("/live/cancel")
async def live_evaluation_cancel(
    body: LiveEvaluationCancelRequest,
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> dict:
    """Clear a pending live evaluation when the validator will not submit any results.

    Call this when the validator bailed out (e.g. no participants, exception, or all evals failed)
    so the dashboard stops showing that evaluation as "EVALUATING".
    """
    await evaluation_repo.delete_live_pending(hotkey, body.evaluation_id)
    await evaluation_repo.finish_evaluation_graph_activity(
        validator_hotkey=hotkey,
        evaluation_id=body.evaluation_id,
        result="cancelled",
    )
    return {"ok": True}


@router.post("", response_model=EvaluationResponse)
async def submit_evaluation(
    evaluation: EvaluationSubmission,
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> EvaluationResponse:
    """Submit evaluation metadata.
    
    Validators call this endpoint after evaluating a sample to store
    the metadata and result in the centralized database.
    
    Args:
        evaluation: Evaluation submission data
        
    Requires validator signature authentication.
    
    Returns:
        Created evaluation record
    """
    # Clear pending first so the dashboard stops showing "EVALUATING" even if store fails
    await evaluation_repo.delete_live_pending(hotkey, evaluation.evaluation_id)

    result = await evaluation_repo.store_evaluation(
        validator_hotkey=hotkey,
        evaluation_id=evaluation.evaluation_id,
        miner_hotkey=evaluation.participant_hotkey,
        s3_bucket=evaluation.s3_bucket,
        s3_prefix=evaluation.s3_prefix,
        wins=evaluation.wins,
        prompt=evaluation.prompt,
        confidence=evaluation.confidence,
        reasoning=evaluation.reasoning,
        original_audio_url=evaluation.original_audio_url,
        generated_audio_url=evaluation.generated_audio_url,
        score=evaluation.score,
        element_scores=evaluation.element_scores,
    )
    await evaluation_repo.finish_evaluation_graph_activity(
        validator_hotkey=hotkey,
        evaluation_id=evaluation.evaluation_id,
        s3_bucket=evaluation.s3_bucket,
        miner_hotkeys=[evaluation.participant_hotkey],
        result="submitted",
    )

    return EvaluationResponse(
        id=result.id,
        evaluation_id=result.evaluation_id,
        participant_hotkey=result.miner_hotkey,
        prompt=result.prompt,
        s3_bucket=result.s3_bucket,
        s3_prefix=result.s3_prefix,
        wins=result.wins,
        confidence=result.confidence,
        reasoning=result.reasoning,
        original_audio_url=result.original_audio_url,
        generated_audio_url=result.generated_audio_url,
        score=result.score,
        element_scores=_decode_element_scores(result.element_scores),
        evaluated_at=result.evaluated_at,
    )


@router.post("/batch", response_model=List[EvaluationResponse])
async def submit_evaluations_batch(
    evaluations: List[EvaluationSubmission],
    hotkey: Annotated[str, Depends(verify_validator_signature)],
) -> List[EvaluationResponse]:
    """Submit multiple evaluations in batch.
    
    Validators can use this endpoint to submit multiple evaluations at once.
    Limited to 100 evaluations per request.
    
    Args:
        evaluations: List of evaluation submissions (max 100)
        
    Requires validator signature authentication.
    
    Returns:
        List of created evaluation records
        
    Raises:
        HTTPException: If batch size exceeds limit
    """
    if len(evaluations) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size exceeds limit of {MAX_BATCH_SIZE} evaluations",
        )
    
    # Clear all pending rows for these evaluation_ids first so dashboard updates even if some stores fail
    seen_eval_ids = {e.evaluation_id for e in evaluations}
    for eval_id in seen_eval_ids:
        await evaluation_repo.delete_live_pending(hotkey, eval_id)
    grouped_miners: dict[str, list[str]] = {}
    grouped_buckets: dict[str, str] = {}
    for evaluation in evaluations:
        grouped_miners.setdefault(evaluation.evaluation_id, []).append(evaluation.participant_hotkey)
        grouped_buckets.setdefault(evaluation.evaluation_id, evaluation.s3_bucket)
    
    results = []
    for evaluation in evaluations:
        result = await evaluation_repo.store_evaluation(
            validator_hotkey=hotkey,
            evaluation_id=evaluation.evaluation_id,
            miner_hotkey=evaluation.participant_hotkey,
            s3_bucket=evaluation.s3_bucket,
            s3_prefix=evaluation.s3_prefix,
            wins=evaluation.wins,
            prompt=evaluation.prompt,
            confidence=evaluation.confidence,
            reasoning=evaluation.reasoning,
            original_audio_url=evaluation.original_audio_url,
            generated_audio_url=evaluation.generated_audio_url,
            score=evaluation.score,
            element_scores=evaluation.element_scores,
        )
        results.append(EvaluationResponse(
            id=result.id,
            evaluation_id=result.evaluation_id,
            participant_hotkey=result.miner_hotkey,
            prompt=result.prompt,
            s3_bucket=result.s3_bucket,
            s3_prefix=result.s3_prefix,
            wins=result.wins,
            confidence=result.confidence,
            reasoning=result.reasoning,
            original_audio_url=result.original_audio_url,
            generated_audio_url=result.generated_audio_url,
            score=result.score,
            element_scores=_decode_element_scores(result.element_scores),
            evaluated_at=result.evaluated_at,
        ))

    for eval_id in seen_eval_ids:
        await evaluation_repo.finish_evaluation_graph_activity(
            validator_hotkey=hotkey,
            evaluation_id=eval_id,
            s3_bucket=grouped_buckets.get(eval_id),
            miner_hotkeys=grouped_miners.get(eval_id, []),
            result="submitted",
        )

    return results
