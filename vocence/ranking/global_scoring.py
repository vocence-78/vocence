"""Shared global scoring helpers used by validators and dashboard metrics."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Dict, List

from vocence.adapters.storage import create_custom_storage_client
from vocence.domain.config import (
    MAX_EVALS_FOR_SCORING,
    MIN_EVALS_TO_COMPETE,
    MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE,
    MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY,
    OWNER_HOTKEY,
    THRESHOLD_MARGIN,
)
from vocence.domain.entities import ParticipantInfo
from vocence.ranking.calculator import calculate_scores_from_samples
from vocence.validator_buckets import ValidatorBucketConfig


def safe_float(value: Any) -> float:
    """Convert scalar/tensor-like values to float."""
    if value is None:
        return 0.0
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except Exception:
            pass
    try:
        return float(value)
    except Exception:
        return 0.0


def validator_stakes_from_metagraph(metagraph: Any) -> Dict[str, float]:
    """Extract validator stakes keyed by hotkey from a metagraph-like object."""
    hotkeys = list(getattr(metagraph, "hotkeys", []) or [])
    raw_stakes = getattr(metagraph, "S", None)
    if raw_stakes is None:
        raw_stakes = getattr(metagraph, "stake", None)
    if raw_stakes is None:
        raw_stakes = getattr(metagraph, "stakes", None)

    stakes: Dict[str, float] = {}
    for uid, hotkey in enumerate(hotkeys):
        raw_value = None
        try:
            if raw_stakes is not None:
                raw_value = raw_stakes[uid]
        except Exception:
            raw_value = None
        stakes[hotkey] = max(0.0, safe_float(raw_value))
    return stakes


def select_active_bucket_configs(
    configs: List[ValidatorBucketConfig],
    active_hotkeys: List[str],
) -> tuple[List[ValidatorBucketConfig], List[str]]:
    """Filter local bucket configs to active validator hotkeys only."""
    config_by_hotkey = {cfg.hotkey: cfg for cfg in configs}
    selected: list[ValidatorBucketConfig] = []
    missing: list[str] = []
    for hotkey in active_hotkeys:
        cfg = config_by_hotkey.get(hotkey)
        if cfg is None:
            missing.append(hotkey)
            continue
        selected.append(cfg)
    return selected, missing


async def collect_validator_bucket_scores(
    bucket_configs: List[ValidatorBucketConfig],
    valid_hotkeys: set[str],
) -> tuple[Dict[str, Dict[str, Dict[str, Any]]], List[dict]]:
    """Read recent metadata from active validator buckets."""

    async def collect_one(cfg: ValidatorBucketConfig) -> tuple[str, Dict[str, Dict[str, Any]]]:
        client = create_custom_storage_client(cfg.access_key, cfg.secret_key)
        scores = await calculate_scores_from_samples(
            client,
            bucket_name=cfg.bucket_name,
            max_evals=MAX_EVALS_FOR_SCORING,
            valid_hotkeys=valid_hotkeys,
        )
        return cfg.hotkey, scores

    tasks = [collect_one(cfg) for cfg in bucket_configs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    collected: Dict[str, Dict[str, Dict[str, Any]]] = {}
    events: List[dict] = []
    for cfg, result in zip(bucket_configs, results):
        if isinstance(result, Exception):
            events.append(
                {
                    "level": "warn",
                    "hotkey": cfg.hotkey,
                    "bucket_name": cfg.bucket_name,
                    "message": str(result),
                }
            )
            continue
        hotkey, scores = result
        collected[hotkey] = scores
        events.append(
            {
                "level": "info",
                "hotkey": hotkey,
                "bucket_name": cfg.bucket_name,
                "miner_count": len(scores),
            }
        )
    return collected, events


def aggregate_global_scores(
    bucket_scores: Dict[str, Dict[str, Dict[str, Any]]],
    validator_stakes: Dict[str, float],
) -> Dict[str, Dict[str, Any]]:
    """Aggregate per-validator miner win rates into one global weighted score."""
    aggregated: Dict[str, Dict[str, Any]] = {}
    base_weights = {
        hotkey: math.sqrt(max(0.0, validator_stakes.get(hotkey, 0.0)))
        for hotkey in bucket_scores.keys()
    }
    has_positive_weight = any(weight > 0 for weight in base_weights.values())

    for validator_hotkey in sorted(bucket_scores.keys()):
        local_scores = bucket_scores[validator_hotkey]
        validator_weight = base_weights[validator_hotkey] if has_positive_weight else 1.0
        if has_positive_weight and validator_weight <= 0:
            continue

        for miner_hotkey, stats in local_scores.items():
            total = int(stats.get("total", 0) or 0)
            if total < MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE:
                continue

            win_rate = float(stats.get("win_rate", 0.0) or 0.0)
            wins = int(stats.get("wins", 0) or 0)
            entry = aggregated.setdefault(
                miner_hotkey,
                {
                    "weighted_sum": 0.0,
                    "weight_sum": 0.0,
                    "total": 0,
                    "wins": 0,
                    "validator_count": 0,
                    "validator_hotkeys": [],
                    "eligible_validator_count": 0,
                    "eligible_validator_hotkeys": [],
                    "weighted_evals": 0.0,
                    "per_validator": {},
                },
            )
            entry["weighted_sum"] += validator_weight * win_rate
            entry["weight_sum"] += validator_weight
            entry["total"] += total
            entry["wins"] += wins
            entry["validator_count"] += 1
            entry["validator_hotkeys"].append(validator_hotkey)
            if total > MIN_EVALS_TO_COMPETE:
                entry["eligible_validator_count"] += 1
                entry["eligible_validator_hotkeys"].append(validator_hotkey)
            entry["weighted_evals"] += validator_weight * total
            entry["per_validator"][validator_hotkey] = {
                "wins": wins,
                "total": total,
                "win_rate": win_rate,
                "weight": validator_weight,
            }

    for miner_hotkey, entry in aggregated.items():
        weight_sum = float(entry.get("weight_sum", 0.0) or 0.0)
        total = int(entry.get("total", 0) or 0)
        entry["win_rate"] = (entry["weighted_sum"] / weight_sum) if weight_sum > 0 else 0.0
        entry["raw_win_rate"] = (entry["wins"] / total) if total > 0 else 0.0
        entry["validator_hotkeys"] = sorted(entry["validator_hotkeys"])
        entry["eligible_validator_hotkeys"] = sorted(entry["eligible_validator_hotkeys"])
    return aggregated


def choose_winner(
    participants: Dict[str, Dict[str, Any]],
    scores: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply the same winner-takes-all + threshold rule as validators."""
    ordered = sorted(participants.keys(), key=lambda hk: participants[hk]["block"])

    def get_total(hk: str) -> int:
        return int(scores.get(hk, {}).get("total", 0) or 0)

    def get_validator_count(hk: str) -> int:
        return int(scores.get(hk, {}).get("validator_count", 0) or 0)

    def get_eligible_validator_count(hk: str) -> int:
        return int(scores.get(hk, {}).get("eligible_validator_count", 0) or 0)

    def get_win_rate(hk: str) -> float:
        return float(scores.get(hk, {}).get("win_rate", 0.0) or 0.0)

    eligible_hks = [
        hk
        for hk in ordered
        if get_eligible_validator_count(hk) >= MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY
    ]
    eligible_set = set(eligible_hks)

    threshold_checks_by_miner: Dict[str, List[Dict[str, Any]]] = {}
    candidates_who_beat_all_earlier: List[str] = []

    for candidate in eligible_hks:
        candidate_rate = get_win_rate(candidate)
        beats_all = True
        checks: List[Dict[str, Any]] = []
        for prior in ordered:
            if participants[prior]["block"] >= participants[candidate]["block"]:
                break
            if get_total(prior) == 0:
                continue
            if prior != OWNER_HOTKEY and prior not in eligible_set:
                continue
            prior_rate = get_win_rate(prior)
            required_rate = prior_rate + THRESHOLD_MARGIN
            passed = candidate_rate >= required_rate
            checks.append(
                {
                    "prior_hotkey": prior,
                    "prior_block": int(participants[prior]["block"]),
                    "prior_rate": prior_rate,
                    "required_rate": required_rate,
                    "candidate_rate": candidate_rate,
                    "passed": passed,
                }
            )
            if not passed:
                beats_all = False
                break
        threshold_checks_by_miner[candidate] = checks
        if beats_all:
            candidates_who_beat_all_earlier.append(candidate)

    leader = None
    if candidates_who_beat_all_earlier:
        leader = max(
            candidates_who_beat_all_earlier,
            key=lambda hk: (
                get_win_rate(hk),
                get_eligible_validator_count(hk),
                float(scores.get(hk, {}).get("weighted_evals", 0.0) or 0.0),
                -participants[hk]["block"],
                hk,
            ),
        )

    return {
        "ordered_hotkeys": ordered,
        "eligible_hotkeys": eligible_hks,
        "eligible_set": eligible_set,
        "leader": leader,
        "threshold_checks_by_miner": threshold_checks_by_miner,
        "candidates_who_beat_all_earlier": candidates_who_beat_all_earlier,
    }


def short_bucket_label(bucket_name: str) -> str:
    prefix = "vocence-samples-"
    if bucket_name.startswith(prefix):
        return bucket_name[len(prefix):]
    return bucket_name


def build_global_scoring_snapshot(
    participant_infos: List[ParticipantInfo],
    selected_bucket_configs: List[ValidatorBucketConfig],
    validator_stakes: Dict[str, float],
    scores: Dict[str, Dict[str, Any]],
    decision: Dict[str, Any],
    generated_at: datetime | None = None,
) -> Dict[str, Any]:
    """Build a structured snapshot for dashboard display."""
    generated_at = generated_at or datetime.now(timezone.utc)
    participants = {
        p.hotkey: {
            "uid": p.uid,
            "block": p.block or 0,
            "model_name": p.model_name,
            "model_revision": p.model_revision,
            "chute_slug": p.chute_slug,
            "chute_id": p.chute_id,
        }
        for p in participant_infos
        if p.is_valid
    }
    leader = decision.get("leader")
    eligible_set = decision.get("eligible_set", set())
    threshold_checks_by_miner = decision.get("threshold_checks_by_miner", {})
    bucket_by_hotkey = {cfg.hotkey: cfg.bucket_name for cfg in selected_bucket_configs}

    ranked_hotkeys = sorted(
        participants.keys(),
        key=lambda hk: (
            -float(scores.get(hk, {}).get("win_rate", -1.0)),
            -int(scores.get(hk, {}).get("eligible_validator_count", 0) or 0),
            -float(scores.get(hk, {}).get("weighted_evals", 0.0) or 0.0),
            participants[hk]["block"],
            hk,
        ),
    )

    miners: List[Dict[str, Any]] = []
    for idx, hotkey in enumerate(ranked_hotkeys, start=1):
        stats = scores.get(hotkey, {})
        total = int(stats.get("total", 0) or 0)
        validator_count = int(stats.get("validator_count", 0) or 0)
        eligible_validator_count = int(stats.get("eligible_validator_count", 0) or 0)
        eligible = hotkey in eligible_set
        threshold_checks = threshold_checks_by_miner.get(hotkey, [])
        threshold_passed = hotkey in decision.get("candidates_who_beat_all_earlier", [])
        status_reason = "No usable samples"
        if total > 0:
            if eligible_validator_count < MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY:
                status_reason = (
                    f"Needs more than {MIN_EVALS_TO_COMPETE} evaluations in at least "
                    f"{MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY} validators"
                )
            elif not threshold_passed:
                failed_check = next((check for check in threshold_checks if not check.get("passed")), None)
                if failed_check:
                    status_reason = (
                        f"Did not clear threshold vs {str(failed_check['prior_hotkey'])[:8]}"
                    )
                else:
                    status_reason = "Eligible but did not clear threshold checks"
            else:
                status_reason = "Winner" if hotkey == leader else "Eligible"

        per_validator_rows: List[Dict[str, Any]] = []
        for validator_hotkey in sorted((stats.get("per_validator", {}) or {}).keys()):
            item = stats["per_validator"][validator_hotkey]
            bucket_name = bucket_by_hotkey.get(validator_hotkey, "")
            per_validator_rows.append(
                {
                    "validator_hotkey": validator_hotkey,
                    "bucket_name": bucket_name,
                    "label": short_bucket_label(bucket_name or validator_hotkey[:8]),
                    "wins": int(item["wins"]),
                    "total": int(item["total"]),
                    "win_rate": float(item["win_rate"]),
                    "weight": float(item["weight"]),
                    "display": (
                        f"{short_bucket_label(bucket_name or validator_hotkey[:8])} "
                        f"{int(item['wins'])}/{int(item['total'])} "
                        f"({float(item['win_rate']):.1%}, w={float(item['weight']):.2f})"
                    ),
                }
            )

        miners.append(
            {
                "rank": idx,
                "hotkey": hotkey,
                "uid": int(participants[hotkey]["uid"]),
                "block": int(participants[hotkey]["block"]),
                "model_name": participants[hotkey]["model_name"],
                "model_revision": participants[hotkey]["model_revision"],
                "chute_slug": participants[hotkey]["chute_slug"],
                "chute_id": participants[hotkey]["chute_id"],
                "weighted_win_rate": float(stats.get("win_rate", 0.0) or 0.0),
                "raw_win_rate": float(stats.get("raw_win_rate", 0.0) or 0.0),
                "wins": int(stats.get("wins", 0) or 0),
                "total": total,
                "validator_count": validator_count,
                "eligible_validator_count": eligible_validator_count,
                "weighted_evals": float(stats.get("weighted_evals", 0.0) or 0.0),
                "eligible": eligible,
                "threshold_passed": threshold_passed,
                "is_winner": hotkey == leader,
                "status_reason": status_reason,
                "per_validator": per_validator_rows,
                "threshold_checks": threshold_checks,
            }
        )

    active_validators = []
    for cfg in selected_bucket_configs:
        stake = float(validator_stakes.get(cfg.hotkey, 0.0) or 0.0)
        active_validators.append(
            {
                "hotkey": cfg.hotkey,
                "bucket_name": cfg.bucket_name,
                "label": short_bucket_label(cfg.bucket_name),
                "stake": stake,
                "weight": math.sqrt(max(0.0, stake)),
            }
        )

    winner = next((miner for miner in miners if miner["is_winner"]), None)
    winner_reason = None
    if winner:
        winner_reason = (
            f"{winner['hotkey'][:8]} won with a weighted win rate of "
            f"{winner['weighted_win_rate']:.1%} across {winner['validator_count']} validators, "
            f"and cleared the {THRESHOLD_MARGIN:.1%} threshold against all earlier eligible miners."
        )

    return {
        "generated_at": generated_at.isoformat(),
        "max_evals_for_scoring": MAX_EVALS_FOR_SCORING,
        "min_evals_to_compete": MIN_EVALS_TO_COMPETE,
        "min_validator_appearances": MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY,
        "min_evals_per_validator": MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE,
        "threshold_margin": THRESHOLD_MARGIN,
        "active_validator_count": len(active_validators),
        "valid_miner_count": len(participants),
        "active_validators": active_validators,
        "winner": winner,
        "winner_reason": winner_reason,
        "miners": miners,
    }
