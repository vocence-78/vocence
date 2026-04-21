"""
Global scoring snapshot background worker.

Periodically reads every active validator's S3 bucket, aggregates per-miner
win rates with stake-weighted scoring, applies the winner-selection rule, and
persists a dashboard-ready snapshot to global_scoring_snapshots.

Runs every METRICS_CALCULATION_INTERVAL seconds (default 600s).
"""

import asyncio
import bittensor as bt

from vocence.domain.config import (
    ACTIVE_VALIDATOR_WINDOW_HOURS,
    CHAIN_NETWORK,
    METRICS_CALCULATION_INTERVAL,
    MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING,
    SUBNET_ID,
)
from vocence.domain.entities import ParticipantInfo
from vocence.shared.logging import emit_log, print_header
from vocence.validator_buckets import load_validator_bucket_configs
from vocence.ranking.global_scoring import (
    aggregate_global_scores,
    build_global_scoring_snapshot,
    choose_winner,
    collect_validator_bucket_scores,
    select_active_bucket_configs,
    validator_stakes_from_metagraph,
)
from vocence.registry.persistence.repositories import (
    GlobalScoringSnapshotRepository,
    ValidatorRepository,
    MinerRepository,
)


class MetricsCalculationTask:
    """Background worker that recomputes the global scoring snapshot from validator buckets."""

    def __init__(self):
        self.validator_repo = ValidatorRepository()
        self.participant_repo = MinerRepository()
        self.global_snapshot_repo = GlobalScoringSnapshotRepository()
        self._running = False

    async def run(self) -> None:
        """Run the global-scoring snapshot worker loop."""
        self._running = True
        emit_log(f"Global scoring worker starting (interval={METRICS_CALCULATION_INTERVAL}s)", "start")

        # Initial delay to let other services start
        await asyncio.sleep(10)

        while self._running:
            try:
                await self._compute_and_store_global_scoring_snapshot()
            except Exception as e:
                emit_log(f"Global scoring error: {e}, will retry in {METRICS_CALCULATION_INTERVAL}s", "error")
                import traceback
                traceback.print_exc()

            await asyncio.sleep(METRICS_CALCULATION_INTERVAL)

    def stop(self) -> None:
        """Stop the worker."""
        self._running = False

    async def _compute_and_store_global_scoring_snapshot(self) -> None:
        """Compute and persist the latest global scoring snapshot for the public dashboard."""
        print_header("Global Scoring Snapshot")

        valid_participants = await self.participant_repo.fetch_valid_miners()
        if not valid_participants:
            emit_log("Global scoring snapshot skipped: no valid participants", "warn")
            return

        active_validator_hotkeys = await self.validator_repo.fetch_active_validator_hotkeys(
            threshold_hours=ACTIVE_VALIDATOR_WINDOW_HOURS
        )
        if len(active_validator_hotkeys) < MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING:
            emit_log(
                "Global scoring snapshot skipped: not enough active validators",
                "warn",
            )
            return

        try:
            bucket_configs = load_validator_bucket_configs()
        except Exception as e:
            emit_log(f"Global scoring snapshot failed: validator bucket config error: {e}", "error")
            return

        selected_bucket_configs, missing_hotkeys = select_active_bucket_configs(
            bucket_configs,
            active_validator_hotkeys,
        )
        if missing_hotkeys:
            emit_log(
                f"Global scoring snapshot missing active validator buckets: {len(missing_hotkeys)}",
                "warn",
            )
        if len(selected_bucket_configs) < MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING:
            emit_log(
                "Global scoring snapshot skipped: too few active validators with bucket access",
                "warn",
            )
            return

        valid_hotkeys = {p.miner_hotkey for p in valid_participants}
        bucket_scores, bucket_events = await collect_validator_bucket_scores(
            selected_bucket_configs,
            valid_hotkeys=valid_hotkeys,
        )
        for event in bucket_events:
            if event.get("level") == "warn":
                emit_log(
                    f"Global scoring snapshot: failed bucket {event.get('bucket_name')} for {str(event.get('hotkey', ''))[:8]}...: {event.get('message')}",
                    "warn",
                )
            else:
                emit_log(
                    f"Global scoring snapshot: loaded {event.get('bucket_name')} ({event.get('miner_count', 0)} miners)",
                    "info",
                )

        if len(bucket_scores) < MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING:
            emit_log(
                "Global scoring snapshot skipped: too few readable validator buckets",
                "warn",
            )
            return

        subtensor = bt.AsyncSubtensor(network=CHAIN_NETWORK)
        try:
            metagraph = await subtensor.metagraph(netuid=SUBNET_ID)
        finally:
            close_fn = getattr(subtensor, "close", None)
            if close_fn:
                maybe = close_fn()
                if asyncio.iscoroutine(maybe):
                    await maybe

        participants = [
            ParticipantInfo(
                uid=p.uid,
                hotkey=p.miner_hotkey,
                model_name=p.model_name or "",
                model_revision=p.model_revision or "",
                chute_id=p.chute_id or "",
                chute_slug=p.chute_slug or "",
                block=p.block or 0,
                is_valid=bool(p.is_valid),
                invalid_reason=p.invalid_reason,
                model_hash=p.model_hash or "",
            )
            for p in valid_participants
        ]
        validator_stakes = validator_stakes_from_metagraph(metagraph)
        scores = aggregate_global_scores(bucket_scores, validator_stakes)
        decision = choose_winner(
            {
                p.hotkey: {
                    "uid": p.uid,
                    "block": p.block or 0,
                    "model_name": p.model_name,
                    "chute_id": p.chute_id,
                }
                for p in participants
            },
            scores,
        )
        snapshot = build_global_scoring_snapshot(
            participant_infos=participants,
            selected_bucket_configs=selected_bucket_configs,
            validator_stakes=validator_stakes,
            scores=scores,
            decision=decision,
        )
        await self.global_snapshot_repo.upsert_latest(snapshot)
        winner = snapshot.get("winner")
        if winner:
            emit_log(
                f"Updated global scoring snapshot: winner {str(winner.get('hotkey', ''))[:8]}... at {winner.get('weighted_win_rate', 0.0):.1%}",
                "success",
            )
        else:
            emit_log("Updated global scoring snapshot: no winner", "info")
