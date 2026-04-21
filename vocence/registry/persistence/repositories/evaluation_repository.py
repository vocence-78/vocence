"""
Validator Evaluations Repository.

Handles evaluation metadata submitted by validators.
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, delete, func, Integer

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import ValidatorEvaluation, LiveEvaluationPending, GraphActivityLease
from vocence.registry.persistence.repositories.graph_activity_repository import GraphActivityRepository
from vocence.shared.logging import emit_log


class EvaluationRepository:
    """Repository for validator_evaluations table."""

    def __init__(self):
        self.graph_repo = GraphActivityRepository()
    
    async def store_evaluation(
        self,
        validator_hotkey: str,
        evaluation_id: str,
        miner_hotkey: str,
        s3_bucket: str,
        s3_prefix: str,
        wins: bool,
        prompt: Optional[str] = None,
        confidence: Optional[int] = None,
        reasoning: Optional[str] = None,
        original_audio_url: Optional[str] = None,
        generated_audio_url: Optional[str] = None,
        score: Optional[float] = None,
        element_scores: Optional[Dict[str, float]] = None,
    ) -> ValidatorEvaluation:
        """Save a validator evaluation.

        Uses upsert logic based on unique constraint.

        Args:
            validator_hotkey: Validator's hotkey
            evaluation_id: Unique evaluation identifier
            miner_hotkey: Miner's hotkey
            s3_bucket: S3 bucket containing evaluation
            s3_prefix: S3 prefix for evaluation files
            wins: Whether generated audio won
            prompt: Generation prompt
            confidence: Confidence percentage
            reasoning: Evaluation reasoning
            score: Continuous weighted score in [0, 1]
            element_scores: Per-element raw score dict (persisted as JSON text)

        Returns:
            ValidatorEvaluation instance
        """
        element_scores_json = (
            json.dumps(element_scores) if element_scores is not None else None
        )
        async with acquire_session() as session:
            # Check for existing
            query = select(ValidatorEvaluation).where(
                ValidatorEvaluation.validator_hotkey == validator_hotkey,
                ValidatorEvaluation.evaluation_id == evaluation_id,
                ValidatorEvaluation.miner_hotkey == miner_hotkey,
            )
            result = await session.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                existing.s3_bucket = s3_bucket
                existing.s3_prefix = s3_prefix
                existing.wins = wins
                existing.prompt = prompt
                existing.confidence = confidence
                existing.reasoning = reasoning
                existing.original_audio_url = original_audio_url
                existing.generated_audio_url = generated_audio_url
                existing.score = score
                existing.element_scores = element_scores_json
                existing.evaluated_at = datetime.now(timezone.utc)
                evaluation = existing
            else:
                evaluation = ValidatorEvaluation(
                    validator_hotkey=validator_hotkey,
                    evaluation_id=evaluation_id,
                    miner_hotkey=miner_hotkey,
                    s3_bucket=s3_bucket,
                    s3_prefix=s3_prefix,
                    wins=wins,
                    prompt=prompt,
                    confidence=confidence,
                    reasoning=reasoning,
                    original_audio_url=original_audio_url,
                    generated_audio_url=generated_audio_url,
                    score=score,
                    element_scores=element_scores_json,
                )
                session.add(evaluation)

            await session.flush()
            return evaluation
    
    async def fetch_by_validator(
        self,
        validator_hotkey: str,
        limit: int = 100,
    ) -> List[ValidatorEvaluation]:
        """Get evaluations submitted by a validator."""
        async with acquire_session() as session:
            query = (
                select(ValidatorEvaluation)
                .where(ValidatorEvaluation.validator_hotkey == validator_hotkey)
                .order_by(ValidatorEvaluation.evaluated_at.desc())
                .limit(limit)
            )
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def fetch_by_miner(
        self,
        miner_hotkey: str,
        limit: int = 100,
    ) -> List[ValidatorEvaluation]:
        """Get all evaluations for a miner across validators."""
        async with acquire_session() as session:
            query = (
                select(ValidatorEvaluation)
                .where(ValidatorEvaluation.miner_hotkey == miner_hotkey)
                .order_by(ValidatorEvaluation.evaluated_at.desc())
                .limit(limit)
            )
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def compute_miner_stats_by_validator(
        self,
        validator_hotkey: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Get miner stats for a specific validator.
        
        Args:
            validator_hotkey: Validator's hotkey
            
        Returns:
            Dict mapping miner_hotkey to {wins, total, win_rate}
        """
        async with acquire_session() as session:
            query = (
                select(
                    ValidatorEvaluation.miner_hotkey,
                    func.count(ValidatorEvaluation.id).label("total"),
                    func.sum(func.cast(ValidatorEvaluation.wins, Integer)).label("wins"),
                )
                .where(ValidatorEvaluation.validator_hotkey == validator_hotkey)
                .group_by(ValidatorEvaluation.miner_hotkey)
            )
            result = await session.execute(query)
            rows = result.all()
            
            stats = {}
            for row in rows:
                total = row.total or 0
                wins = row.wins or 0
                stats[row.miner_hotkey] = {
                    "wins": wins,
                    "total": total,
                    "win_rate": wins / total if total > 0 else 0.0,
                }
            
            return stats

    async def compute_miner_stats_by_validator_recent(
        self,
        validator_hotkey: str,
        max_evals: int = 100,
    ) -> Dict[str, Dict[str, Any]]:
        """Get miner stats for a validator using only the most recent max_evals evaluations.
        Matches validator-side logic: same evaluation_id ordering (most recent first) and window size.
        """
        async with acquire_session() as session:
            subq = (
                select(ValidatorEvaluation.evaluation_id)
                .where(ValidatorEvaluation.validator_hotkey == validator_hotkey)
                .distinct()
                .order_by(ValidatorEvaluation.evaluation_id.desc())
                .limit(max_evals)
            )
            query = (
                select(
                    ValidatorEvaluation.miner_hotkey,
                    func.count(ValidatorEvaluation.id).label("total"),
                    func.sum(func.cast(ValidatorEvaluation.wins, Integer)).label("wins"),
                )
                .where(
                    ValidatorEvaluation.validator_hotkey == validator_hotkey,
                    ValidatorEvaluation.evaluation_id.in_(subq),
                )
                .group_by(ValidatorEvaluation.miner_hotkey)
            )
            result = await session.execute(query)
            rows = result.all()
            stats = {}
            for row in rows:
                total = row.total or 0
                wins = row.wins or 0
                stats[row.miner_hotkey] = {
                    "wins": wins,
                    "total": total,
                    "win_rate": wins / total if total > 0 else 0.0,
                }
            return stats

    async def compute_all_miner_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get aggregated miner stats across all validators.
        
        Returns:
            Dict mapping miner_hotkey to {wins, total, win_rate, validator_count}
        """
        async with acquire_session() as session:
            query = (
                select(
                    ValidatorEvaluation.miner_hotkey,
                    func.count(ValidatorEvaluation.id).label("total"),
                    func.sum(func.cast(ValidatorEvaluation.wins, Integer)).label("wins"),
                    func.count(func.distinct(ValidatorEvaluation.validator_hotkey)).label("validator_count"),
                )
                .group_by(ValidatorEvaluation.miner_hotkey)
            )
            result = await session.execute(query)
            rows = result.all()
            
            stats = {}
            for row in rows:
                total = row.total or 0
                wins = row.wins or 0
                stats[row.miner_hotkey] = {
                    "wins": wins,
                    "total": total,
                    "win_rate": wins / total if total > 0 else 0.0,
                    "validator_count": row.validator_count,
                }
            
            return stats
    
    async def count_by_validator(self, validator_hotkey: str) -> int:
        """Get evaluation count for a validator."""
        async with acquire_session() as session:
            result = await session.execute(
                select(func.count(ValidatorEvaluation.id)).where(
                    ValidatorEvaluation.validator_hotkey == validator_hotkey
                )
            )
            return result.scalar_one()
    
    async def count_total(self) -> int:
        """Get total evaluation count across all validators."""
        async with acquire_session() as session:
            result = await session.execute(select(func.count(ValidatorEvaluation.id)))
            return result.scalar_one()

    # ----- Live evaluation pending (dashboard status bar) -----

    async def add_live_pending(
        self,
        validator_hotkey: str,
        evaluation_id: str,
        prompt_summary: Optional[str] = None,
        miner_hotkeys: Optional[List[str]] = None,
    ) -> LiveEvaluationPending:
        """Upsert a live evaluation pending row (one per validator+evaluation_id)."""
        import json
        async with acquire_session() as session:
            query = select(LiveEvaluationPending).where(
                LiveEvaluationPending.validator_hotkey == validator_hotkey,
                LiveEvaluationPending.evaluation_id == evaluation_id,
            )
            result = await session.execute(query)
            existing = result.scalar_one_or_none()
            raw_miners = json.dumps(miner_hotkeys) if miner_hotkeys else None
            if existing:
                existing.prompt_summary = prompt_summary
                existing.miner_hotkeys = raw_miners
                await session.flush()
                return existing
            row = LiveEvaluationPending(
                validator_hotkey=validator_hotkey,
                evaluation_id=evaluation_id,
                prompt_summary=prompt_summary,
                miner_hotkeys=raw_miners,
            )
            session.add(row)
            await session.flush()
            return row

    async def delete_live_pending(self, validator_hotkey: str, evaluation_id: str) -> int:
        """Remove pending row when evaluations are submitted. Returns deleted count."""
        async with acquire_session() as session:
            result = await session.execute(
                delete(LiveEvaluationPending).where(
                    LiveEvaluationPending.validator_hotkey == validator_hotkey,
                    LiveEvaluationPending.evaluation_id == evaluation_id,
                )
            )
            await session.flush()
            return result.rowcount or 0

    async def get_live_pending_by_validator(
        self, validator_hotkey: str, limit: int = 20
    ) -> List[LiveEvaluationPending]:
        """Get pending evaluations for a validator (for dashboard status bar)."""
        async with acquire_session() as session:
            query = (
                select(LiveEvaluationPending)
                .where(LiveEvaluationPending.validator_hotkey == validator_hotkey)
                .order_by(LiveEvaluationPending.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def start_evaluation_graph_activity(
        self,
        validator_hotkey: str,
        evaluation_id: str,
        miner_hotkeys: Optional[List[str]] = None,
        prompt_summary: Optional[str] = None,
    ) -> None:
        await self.graph_repo.upsert_lease(
            activity_type="evaluation",
            activity_key=f"eval:{validator_hotkey}:{evaluation_id}",
            validator_hotkey=validator_hotkey,
            payload={
                "evaluation_id": evaluation_id,
                "miner_hotkeys": list(miner_hotkeys or []),
                "prompt_summary": prompt_summary,
            },
            ttl_seconds=180,
            status="active",
        )

    async def finish_evaluation_graph_activity(
        self,
        validator_hotkey: str,
        evaluation_id: str,
        s3_bucket: Optional[str] = None,
        miner_hotkeys: Optional[List[str]] = None,
        result: str = "submitted",
    ) -> None:
        now = datetime.now(timezone.utc)
        payload_json = json.dumps(
            {
                "evaluation_id": evaluation_id,
                "miner_hotkeys": list(miner_hotkeys or []),
                "s3_bucket": s3_bucket,
                "result": result,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        async with acquire_session() as session:
            result_key = f"eval-result:{validator_hotkey}:{evaluation_id}"
            existing = await session.execute(
                select(GraphActivityLease).where(GraphActivityLease.activity_key == result_key)
            )
            result_lease = existing.scalar_one_or_none()
            expires_at = now + timedelta(seconds=20)

            if result_lease:
                result_lease.activity_type = "evaluation_result"
                result_lease.validator_hotkey = validator_hotkey
                result_lease.status = "complete"
                result_lease.payload_json = payload_json
                result_lease.expires_at = expires_at
                result_lease.updated_at = now
            else:
                session.add(
                    GraphActivityLease(
                        activity_type="evaluation_result",
                        activity_key=result_key,
                        validator_hotkey=validator_hotkey,
                        status="complete",
                        payload_json=payload_json,
                        started_at=now,
                        expires_at=expires_at,
                        updated_at=now,
                    )
                )

            await session.execute(
                delete(GraphActivityLease).where(
                    GraphActivityLease.activity_key == f"eval:{validator_hotkey}:{evaluation_id}"
                )
            )
            await session.flush()
