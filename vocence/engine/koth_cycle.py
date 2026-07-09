"""KOTH evaluation cycle — turn a duel outcome into on-chain weights.

Composition layer that sits between the deterministic pieces (gauntlet, duel, court)
and the chain. The pure decision functions here are what a validator uses each cycle:

1. resolve the current reign (slot-ordered court; slot 1 is the lead king);
2. pick the highest-priority validated challenger;
3. duel it against the lead king (see :mod:`vocence.pipeline.duel`);
4. :func:`plan_after_duel` -> the (uids, weights) to set on chain.

Kept free of chain/GPU coupling so the coronation logic is unit-testable; the running
coordinator injects the real reveal-reader, validator, duel runner, and weight-setter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.dense_scoring import DuelResult
from vocence.ranking.koth import (
    ReignMember, PlannedMember, build_reign_plan,
    weight_bps_for_member_count, weight_epoch_payload,
)


@dataclass(frozen=True)
class Candidate:
    """A validated on-chain submission eligible to challenge for the crown."""

    uid: int
    hotkey: str
    repo: str
    digest: str
    model_hash: str
    submission_id: str = ""
    block: int = 0


def lead_king(reign: Sequence[ReignMember]) -> Optional[ReignMember]:
    """The slot-1 king a challenger must beat, or None for an empty reign (genesis)."""
    return next((m for m in reign if m.slot == 1), None)


def _even_split_plan(reign: Sequence[ReignMember]) -> List[PlannedMember]:
    ordered = sorted(reign, key=lambda m: m.slot or 99)
    bps = weight_bps_for_member_count(len(ordered))
    return [
        PlannedMember(member=m, slot=i + 1, weight_bps=bps[i], is_challenger=False)
        for i, m in enumerate(ordered)
    ]


def current_reign_weights(reign: Sequence[ReignMember], spec: SubnetSpec) -> Tuple[List[int], List[float]]:
    """(uids, weights) for the incumbent court unchanged — used when no one is crowned."""
    if not reign:
        return [spec.burn_uid], [1.0]
    return weight_epoch_payload(_even_split_plan(reign), burn_uid=spec.burn_uid)


def challenger_from_candidate(candidate: Candidate) -> ReignMember:
    return ReignMember(
        uid=candidate.uid, hotkey=candidate.hotkey, model_hash=candidate.model_hash,
        submission_id=candidate.submission_id,
    )


def plan_after_duel(
    reign: Sequence[ReignMember],
    challenger: ReignMember,
    result: DuelResult,
    spec: SubnetSpec,
) -> Tuple[List[int], List[float], bool]:
    """Decide the weights after a duel.

    Coronation (challenger crowned) -> new top-5 court via ``build_reign_plan``.
    Otherwise the incumbent court's weights are retained (or a burn for an empty reign).
    Returns ``(uids, weights, coronated)``.
    """
    if result.state == "succeeded" and result.challenger_won:
        plan = build_reign_plan(reign, challenger, court_size=spec.court_size)
        uids, weights = weight_epoch_payload(plan, burn_uid=spec.burn_uid)
        return uids, weights, True
    uids, weights = current_reign_weights(reign, spec)
    return uids, weights, False


def select_challenger(candidates: Sequence[Candidate], reign: Sequence[ReignMember]) -> Optional[Candidate]:
    """Pick the next challenger: earliest-committed candidate not already the lead king.

    Earliest block first (fair queueing, matches Albedo's priority/created_at ordering);
    skips a candidate whose model already sits at slot 1.
    """
    king = lead_king(reign)
    pool = [
        c for c in candidates
        if not (king and c.hotkey == king.hotkey and c.model_hash == king.model_hash)
    ]
    if not pool:
        return None
    return sorted(pool, key=lambda c: (c.block, c.uid))[0]
