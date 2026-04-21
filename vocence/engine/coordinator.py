"""
Validator engine service for Vocence.

Sets weights based on miner performance from the validator's own S3 samples bucket.
Implements winner-take-all scoring with "beat predecessors by threshold" rule.

Architecture:
- Validators submit sample metadata to API (for dashboard tracking)
- Validators upload samples to their own Hippius S3 bucket
- Validators calculate scores from their own S3 samples
- Validators set weights based on their own calculations
"""

import asyncio
from typing import Dict, Any, List

import bittensor as bt
from minio import Minio
from openai import AsyncOpenAI

from vocence.domain.config import (
    API_URL,
    SUBNET_ID,
    CYCLE_LENGTH,
    CYCLE_OFFSET_BLOCKS,
    CYCLE_BLOCK_TOLERANCE,
    SUBTENSOR_TIMEOUT_SEC,
    MIN_EVALS_TO_COMPETE,
    THRESHOLD_MARGIN,
    MAX_EVALS_FOR_SCORING,
    BURN_UID,
    CHUTES_AUTH_KEY,
    OPENAI_AUTH_KEY,
    COLDKEY_NAME,
    HOTKEY_NAME,
    CHAIN_NETWORK,
    AUDIO_SOURCE_BUCKET,
    AUDIO_SAMPLES_BUCKET,
    VALIDATOR_ID,
    SAMPLE_SLOT_INTERVAL_BLOCKS,
    SAMPLE_SLOT_OFFSET_BLOCKS,
    OWNER_HOTKEY,
    MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING,
    MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY,
    MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE,
)
from vocence.shared.logging import emit_log, print_header, print_table
from vocence.domain.entities import ParticipantInfo
from vocence.adapters.storage import (
    create_corpus_storage_client,
    create_validator_storage_client,
)
from vocence.ranking.global_scoring import (
    aggregate_global_scores,
    build_global_scoring_snapshot,
    choose_winner,
    collect_validator_bucket_scores,
    select_active_bucket_configs,
    validator_stakes_from_metagraph,
)
from vocence.pipeline.generation import generate_samples_continuously
from vocence.validator_buckets import ValidatorBucketConfig, load_validator_bucket_configs


# Track last cycle we executed so we only run once per cycle when block is in tolerance window
_last_executed_cycle_block: int | None = None


async def fetch_participants_from_api() -> List[ParticipantInfo]:
    """Get valid participants from the centralized API.
    
    Returns:
        List of valid ParticipantInfo objects
    """
    try:
        from vocence.adapters.api import create_service_client_from_wallet
        
        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME,
            hotkey_name=HOTKEY_NAME,
            api_url=API_URL,
        )
        
        try:
            miners = await client.get_valid_miners()
            return miners
        finally:
            await client.close()
    except Exception as e:
        emit_log(f"Failed to get miners from API: {e}", "warn")
        return []


async def fetch_active_validators_from_api() -> List[str]:
    """Get active validator hotkeys from the centralized API."""
    try:
        from vocence.adapters.api import create_service_client_from_wallet

        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME,
            hotkey_name=HOTKEY_NAME,
            api_url=API_URL,
        )
        try:
            return await client.get_active_validators()
        finally:
            await client.close()
    except Exception as e:
        emit_log(f"Failed to get active validators from API: {e}", "warn")
        return []


async def _send_weight_setting_graph_event(
    block: int,
    phase: str,
    target_validator_hotkeys: List[str] | None = None,
    result: str | None = None,
    winner_hotkey: str | None = None,
) -> None:
    """Best-effort graph event for weight-setting lifecycle."""
    try:
        from vocence.adapters.api import create_service_client_from_wallet

        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME,
            hotkey_name=HOTKEY_NAME,
            api_url=API_URL,
        )
        try:
            if result is None:
                await client.start_weight_setting(
                    cycle_block=block,
                    target_validator_hotkeys=target_validator_hotkeys or [],
                    phase=phase,
                )
            else:
                await client.finish_weight_setting(
                    cycle_block=block,
                    result=result,
                    winner_hotkey=winner_hotkey,
                )
        finally:
            await client.close()
    except Exception as e:
        emit_log(f"[{block}] Weight-setting graph event failed ({phase}): {e}", "warn")


def _short_bucket_label(bucket_name: str) -> str:
    prefix = "vocence-samples-"
    if bucket_name.startswith(prefix):
        return bucket_name[len(prefix):]
    return bucket_name


def _log_score_breakdown_table(
    participants: Dict[str, Dict[str, Any]],
    scores: Dict[str, Dict[str, Any]],
    bucket_configs: List[ValidatorBucketConfig],
) -> None:
    """Render a detailed per-miner score table."""
    if not participants:
        return

    bucket_name_by_hotkey = {cfg.hotkey: cfg.bucket_name for cfg in bucket_configs}
    rows: list[list[str]] = []

    ordered_hotkeys = sorted(
        participants.keys(),
        key=lambda hk: (
            -float(scores.get(hk, {}).get("win_rate", -1.0)),
            participants[hk]["block"],
            hk,
        ),
    )

    for miner_hotkey in ordered_hotkeys:
        stats = scores.get(miner_hotkey)
        if not stats:
            rows.append(
                [
                    miner_hotkey[:8],
                    str(participants[miner_hotkey]["block"]),
                    "n/a",
                    "0/0",
                    "0",
                    "no usable samples",
                ]
            )
            continue

        per_validator = stats.get("per_validator", {}) or {}
        details: list[str] = []
        for validator_hotkey in sorted(per_validator.keys()):
            item = per_validator[validator_hotkey]
            bucket_name = bucket_name_by_hotkey.get(validator_hotkey, validator_hotkey[:8])
            details.append(
                f"{_short_bucket_label(bucket_name)} {item['wins']}/{item['total']} "
                f"({item['win_rate']:.1%}, w={item['weight']:.2f})"
            )

        rows.append(
            [
                miner_hotkey[:8],
                str(participants[miner_hotkey]["block"]),
                f"{float(stats.get('win_rate', 0.0)):.1%}",
                f"{int(stats.get('wins', 0))}/{int(stats.get('total', 0))}",
                str(int(stats.get("validator_count", 0))),
                "\n".join(details) if details else "no validator contributions",
            ]
        )

    print_table(
        title="Global Score Breakdown",
        columns=["Miner", "Block", "Weighted", "Raw", "Vals", "Per-validator detail"],
        rows=rows,
    )


def _log_winner_decision_table(
    leader: str,
    participants: Dict[str, Dict[str, Any]],
    scores: Dict[str, Dict[str, Any]],
    ordered: List[str],
    eligible_set: set[str],
) -> None:
    """Render how the winner cleared the threshold against earlier miners."""
    leader_rate = float(scores.get(leader, {}).get("win_rate", 0.0) or 0.0)
    rows: list[list[str]] = []

    for prior in ordered:
        if participants[prior]["block"] >= participants[leader]["block"]:
            break
        prior_total = int(scores.get(prior, {}).get("total", 0) or 0)
        if prior_total == 0:
            continue
        if prior != OWNER_HOTKEY and prior not in eligible_set:
            continue

        prior_rate = float(scores.get(prior, {}).get("win_rate", 0.0) or 0.0)
        required_rate = prior_rate + THRESHOLD_MARGIN
        passed = leader_rate >= required_rate
        rows.append(
            [
                prior[:8],
                str(participants[prior]["block"]),
                f"{prior_rate:.1%}",
                f"{required_rate:.1%}",
                f"{leader_rate:.1%}",
                "yes" if passed else "no",
            ]
        )

    if rows:
        print_table(
            title=f"Winner Threshold Checks For {leader[:8]}",
            columns=["Prior miner", "Block", "Prior rate", "Need at least", "Leader rate", "Pass"],
            rows=rows,
        )


async def execute_cycle(
    subtensor_ref: dict,
    wallet: bt.Wallet,
    storage_client: Minio,
    block: int,
) -> None:
    """Set weights based on miner performance.
    
    - Gets valid miners from centralized API
    - Calculates scores from validator's own S3 samples bucket
    - Sets weights based on own calculations
    
    Args:
        subtensor_ref: Mutable ref containing current AsyncSubtensor (so we can reconnect on timeout)
        wallet: Bittensor wallet for signing transactions
        storage_client: Minio client for validator's Hippius S3
        block: Current block number
    """
    subtensor = subtensor_ref["client"]
    _ = storage_client  # generation still uses the local validator bucket; scoring now reads all active validator buckets
    emit_log(f"[{block}] Fetching participants and active validators from API", "info")
    await _send_weight_setting_graph_event(block, phase="fetching_inputs")

    try:
        participant_infos = await fetch_participants_from_api()
    except Exception as e:
        emit_log(f"[{block}] Failed to get participants from API: {e}", "error")
        return

    if not participant_infos:
        emit_log(f"[{block}] No participant commitments found", "warn")
        return

    try:
        active_validator_hotkeys = await fetch_active_validators_from_api()
    except Exception as e:
        emit_log(f"[{block}] Failed to get active validators from API: {e}", "error")
        return

    valid_participants = [p for p in participant_infos if p.is_valid]
    invalid_count = len(participant_infos) - len(valid_participants)
    if not valid_participants:
        emit_log(f"[{block}] No valid participants ({invalid_count} invalid)", "warn")
        return

    emit_log(f"[{block}] Found {len(valid_participants)} valid participants ({invalid_count} invalid)", "info")

    participants = {
        p.hotkey: {"block": p.block or 0, "model_name": p.model_name, "chute_id": p.chute_id}
        for p in valid_participants
    }
    valid_hotkeys = set(participants.keys())

    try:
        bucket_configs = load_validator_bucket_configs()
    except Exception as e:
        emit_log(f"[{block}] Failed to load validator bucket config: {e}", "error")
        bucket_configs = []

    if len(active_validator_hotkeys) < MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING:
        emit_log(
            f"[{block}] Only {len(active_validator_hotkeys)} active validators from API; need at least {MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING}. Burning this cycle.",
            "warn",
        )
        selected_bucket_configs: list[ValidatorBucketConfig] = []
    else:
        selected_bucket_configs, missing_hotkeys = select_active_bucket_configs(bucket_configs, active_validator_hotkeys)
        if missing_hotkeys:
            emit_log(
                f"Active validator hotkeys missing from local bucket config: {len(missing_hotkeys)} ({', '.join(hk[:8] for hk in missing_hotkeys[:5])})",
                "warn",
            )

    if len(selected_bucket_configs) < MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING:
        emit_log(
            f"[{block}] Only {len(selected_bucket_configs)} active validators have local bucket credentials; need at least {MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING}. Burning this cycle.",
            "warn",
        )
        bucket_scores: Dict[str, Dict[str, Dict[str, Any]]] = {}
    else:
        await _send_weight_setting_graph_event(
            block,
            phase="scoring",
            target_validator_hotkeys=[cfg.hotkey for cfg in selected_bucket_configs],
        )
        emit_log(
            f"[{block}] Reading recent {MAX_EVALS_FOR_SCORING} evaluations from {len(selected_bucket_configs)} active validator buckets",
            "info",
        )
        bucket_scores, bucket_events = await collect_validator_bucket_scores(
            selected_bucket_configs,
            valid_hotkeys=valid_hotkeys,
        )
        for event in bucket_events:
            if event.get("level") == "warn":
                emit_log(
                    f"Failed to read validator bucket for {str(event.get('hotkey', ''))[:8]}... ({event.get('bucket_name')}): {event.get('message')}",
                    "warn",
                )
            else:
                emit_log(
                    f"Loaded validator bucket {event.get('bucket_name')} for {str(event.get('hotkey', ''))[:8]}... ({event.get('miner_count', 0)} miners with recent data)",
                    "info",
                )

    try:
        metagraph = await asyncio.wait_for(subtensor.metagraph(SUBNET_ID), timeout=SUBTENSOR_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        emit_log(f"[{block}] Timed out fetching metagraph (>{SUBTENSOR_TIMEOUT_SEC}s), reconnecting subtensor...", "error")
        await _reconnect_subtensor(subtensor_ref)
        return
    except Exception as e:
        emit_log(f"[{block}] Failed to fetch metagraph: {e}", "error")
        return

    validator_stakes = validator_stakes_from_metagraph(metagraph)
    scores = aggregate_global_scores(bucket_scores, validator_stakes)
    if not scores:
        emit_log(f"[{block}] No usable global evaluation data found across active validator buckets", "warn")

    for hotkey in participants:
        if hotkey in scores:
            s = scores[hotkey]
            emit_log(
                f"  {hotkey[:8]}: weighted={s['win_rate']:.1%}, raw={s['wins']}/{s['total']} ({s['validator_count']} validators)",
                "info",
            )
        else:
            emit_log(f"  {hotkey[:8]}: no usable global samples yet", "info")

    if scores:
        _log_score_breakdown_table(
            participants=participants,
            scores=scores,
            bucket_configs=selected_bucket_configs,
        )

    decision = choose_winner(participants, scores)
    ordered = decision["ordered_hotkeys"]
    eligible_set = decision["eligible_set"]
    leader = decision["leader"]

    if leader is None:
        emit_log(
            f"[{block}] No globally eligible miner satisfied consensus + margin rules; setting weight 1 on UID {BURN_UID} (burn)",
            "success",
        )
        try:
            await asyncio.wait_for(
                subtensor.set_weights(wallet=wallet, netuid=SUBNET_ID, uids=[BURN_UID], weights=[1.0], wait_for_inclusion=True),
                timeout=SUBTENSOR_TIMEOUT_SEC,
            )
            emit_log(f"[{block}] Set weight 1 on UID {BURN_UID} (burn)", "success")
            await _send_weight_setting_graph_event(block, phase="completed", result="burn")
        except asyncio.TimeoutError:
            emit_log(f"[{block}] Timed out setting weights (>{SUBTENSOR_TIMEOUT_SEC}s), reconnecting subtensor...", "error")
            await _send_weight_setting_graph_event(block, phase="failed", result="failed")
            await _reconnect_subtensor(subtensor_ref)
        except Exception as e:
            emit_log(f"[{block}] Failed to set weights (burn): {e}", "error")
            await _send_weight_setting_graph_event(block, phase="failed", result="failed")
        return

    leader_rate = float(scores.get(leader, {}).get("win_rate", 0.0) or 0.0)
    leader_stats = scores.get(leader, {})
    _log_winner_decision_table(
        leader=leader,
        participants=participants,
        scores=scores,
        ordered=ordered,
        eligible_set=eligible_set,
    )
    emit_log(
        f"[{block}] Winner: {leader[:8]} weighted_win_rate={leader_rate:.1%}, raw={leader_stats.get('wins', 0)}/{leader_stats.get('total', 0)}, validators={leader_stats.get('validator_count', 0)}",
        "success",
    )

    uids, weights = [], []
    for uid, hotkey in enumerate(metagraph.hotkeys):
        if hotkey in participants:
            uids.append(uid)
            weights.append(1.0 if hotkey == leader else 0.0)
    if uids:
        try:
            await asyncio.wait_for(
                subtensor.set_weights(wallet=wallet, netuid=SUBNET_ID, uids=uids, weights=weights, wait_for_inclusion=True),
                timeout=SUBTENSOR_TIMEOUT_SEC,
            )
            emit_log(f"[{block}] Set weights for {len(uids)} participants (winner takes all)", "success")
            await _send_weight_setting_graph_event(block, phase="completed", result="success", winner_hotkey=leader)
        except asyncio.TimeoutError:
            emit_log(f"[{block}] Timed out setting weights (>{SUBTENSOR_TIMEOUT_SEC}s), reconnecting subtensor...", "error")
            await _send_weight_setting_graph_event(block, phase="failed", result="failed")
            await _reconnect_subtensor(subtensor_ref)
        except Exception as e:
            emit_log(f"[{block}] Failed to set weights: {e}", "error")
            await _send_weight_setting_graph_event(block, phase="failed", result="failed")


async def _reconnect_subtensor(subtensor_ref: dict) -> None:
    """Replace the subtensor in ref with a new instance (reconnect after drop)."""
    old = subtensor_ref.get("client")
    if old is not None:
        try:
            if hasattr(old, "close"):
                close_fn = getattr(old, "close")
                if asyncio.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
        except Exception:
            pass
    subtensor_ref["client"] = bt.AsyncSubtensor(network=CHAIN_NETWORK)
    emit_log("Reconnected subtensor (new connection)", "info")


async def cycle_step(subtensor_ref: dict, wallet: bt.Wallet, storage_client: Minio) -> None:
    """Wait for cycle boundary (within block tolerance) and run weight setting once per cycle.
    
    Uses a block range [expected - CYCLE_BLOCK_TOLERANCE, expected + CYCLE_BLOCK_TOLERANCE]
    so we don't miss the cycle if get_current_block was briefly unavailable. Executes at most
    once per logical cycle. All subtensor calls are wrapped with SUBTENSOR_TIMEOUT_SEC so a
    dropped connection doesn't hang forever. On timeout, creates a new AsyncSubtensor so the
    next attempt uses a fresh connection.
    """
    global _last_executed_cycle_block
    subtensor = subtensor_ref["client"]
    try:
        current_block = await asyncio.wait_for(subtensor.get_current_block(), timeout=SUBTENSOR_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        emit_log(f"get_current_block timed out (>{SUBTENSOR_TIMEOUT_SEC}s), reconnecting subtensor...", "error")
        await _reconnect_subtensor(subtensor_ref)
        await asyncio.sleep(12)
        return
    except Exception as e:
        emit_log(f"get_current_block failed: {e}; reconnecting subtensor...", "error")
        await _reconnect_subtensor(subtensor_ref)
        await asyncio.sleep(12)
        return

    # Cycle block for "this" period: the unique cycle_block in [current - tolerance, current + tolerance] if any
    k = (current_block - CYCLE_OFFSET_BLOCKS) // CYCLE_LENGTH
    cycle_block = CYCLE_OFFSET_BLOCKS + k * CYCLE_LENGTH
    in_window = (cycle_block - CYCLE_BLOCK_TOLERANCE <= current_block <= cycle_block + CYCLE_BLOCK_TOLERANCE)

    if not in_window or _last_executed_cycle_block == cycle_block:
        if not in_window:
            emit_log(f"Block {current_block}: waiting for cycle window (target {cycle_block} ±{CYCLE_BLOCK_TOLERANCE})", "info")
        # Re-poll after one block time so we don't hammer the node; or advance past current window
        await asyncio.sleep(12)
        return

    cycle_num = (current_block - CYCLE_OFFSET_BLOCKS) // CYCLE_LENGTH
    print_header(f"Vocence Cycle #{cycle_num} (block {current_block}, window {cycle_block} ±{CYCLE_BLOCK_TOLERANCE})")
    emit_log(f"Weight-setting cycle (every {CYCLE_LENGTH} blocks, offset {CYCLE_OFFSET_BLOCKS})", "info")
    _last_executed_cycle_block = cycle_block
    try:
        await execute_cycle(subtensor_ref, wallet, storage_client, current_block)
    except Exception as e:
        emit_log(f"[{current_block}] Cycle failed ({e}), will retry next cycle", "error")
        _last_executed_cycle_block = None  # allow retry next time we're in window
        import traceback
        traceback.print_exc()
    else:
        await asyncio.sleep(12)


async def main() -> None:
    """Main entry point for the validator."""
    print_header("Vocence Validator Starting")
    
    # Check required environment variables
    if not CHUTES_AUTH_KEY:
        emit_log("CHUTES_AUTH_KEY environment variable required", "error")
        return
    if not OPENAI_AUTH_KEY:
        emit_log("OPENAI_AUTH_KEY environment variable required", "error")
        return
    
    emit_log(f"Using centralized API for miners: {API_URL}", "info")
    emit_log(f"Using corpus bucket (read) and own samples bucket for scoring: {AUDIO_SAMPLES_BUCKET}", "info")
    
    # Initialize clients (validator: two Hippius credential sets)
    # Use a ref so we can replace the subtensor on timeout (reconnect)
    emit_log("Initializing clients...", "info")
    subtensor_ref: dict = {"client": bt.AsyncSubtensor(network=CHAIN_NETWORK)}
    wallet = bt.Wallet(name=COLDKEY_NAME, hotkey=HOTKEY_NAME)
    corpus_client = create_corpus_storage_client()
    validator_client = create_validator_storage_client()
    openai_client = AsyncOpenAI(api_key=OPENAI_AUTH_KEY)
    
    # Log configuration
    emit_log(f"Wallet: {COLDKEY_NAME}/{HOTKEY_NAME}", "info")
    emit_log(f"Network: {CHAIN_NETWORK}", "info")
    emit_log(f"Subnet ID: {SUBNET_ID}", "info")
    emit_log(f"Cycle length: {CYCLE_LENGTH} blocks, offset {CYCLE_OFFSET_BLOCKS} (~{CYCLE_LENGTH * 12}s)", "info")
    emit_log(f"Sample slots: every {SAMPLE_SLOT_INTERVAL_BLOCKS} blocks at offset {SAMPLE_SLOT_OFFSET_BLOCKS} (validator_id={VALIDATOR_ID})", "info")
    emit_log(f"Corpus bucket (read): s3://{AUDIO_SOURCE_BUCKET}", "info")
    emit_log(f"Samples bucket (own): s3://{AUDIO_SAMPLES_BUCKET}", "info")
    emit_log(f"Min evals to compete: {MIN_EVALS_TO_COMPETE}", "info")
    emit_log(f"Threshold margin: {THRESHOLD_MARGIN}", "info")
    emit_log(f"Max evals for scoring (recent window): {MAX_EVALS_FOR_SCORING}", "info")
    emit_log(f"Cycle/slot block tolerance: ±{CYCLE_BLOCK_TOLERANCE}, subtensor timeout: {SUBTENSOR_TIMEOUT_SEC}s", "info")
    
    emit_log("Starting sample generation loop in background...", "start")

    async def get_block_with_timeout() -> int:
        """Wrap get_current_block with timeout; on timeout reconnect so generator gets fresh connection."""
        try:
            return await asyncio.wait_for(subtensor_ref["client"].get_current_block(), timeout=SUBTENSOR_TIMEOUT_SEC)
        except (asyncio.TimeoutError, Exception):
            emit_log("get_current_block failed in generator, reconnecting subtensor...", "warn")
            await _reconnect_subtensor(subtensor_ref)
            raise

    generator_task = asyncio.create_task(
        generate_samples_continuously(corpus_client, validator_client, openai_client, get_block_with_timeout)
    )
    
    def handle_generator_exception(task: asyncio.Task) -> None:
        """Handle exceptions from the background generator task."""
        try:
            exc = task.exception()
            if exc is not None:
                emit_log(f"Background generator task failed: {exc}", "error")
        except asyncio.CancelledError:
            emit_log("Background generator task was cancelled", "warn")
    
    generator_task.add_done_callback(handle_generator_exception)
    
    emit_log("Starting weight setting loop...", "start")
    while True:
        await cycle_step(subtensor_ref, wallet, validator_client)


def main_sync() -> None:
    """Synchronous entry point for CLI."""
    asyncio.run(main())
