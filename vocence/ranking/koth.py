"""Top-5 king-of-the-hill court — the incentive core.

A challenger duels only the reigning **slot-1 lead king**. If its composite score
beats the lead king's by :data:`SubnetSpec.win_margin`, it is crowned: it enters at
slot 1, existing kings shift down one slot, the court is capped at ``court_size``
(default 5), and any overflow is retired. Emissions are split **evenly** across the
court (``10000 // n`` basis points each). An empty court burns to ``burn_uid``.

This mirrors Albedo's ``build_reign_plan`` / ``weight_bps_for_member_count`` but is a
pure, dependency-free module so it is trivially unit-testable and identical across
every validator (a requirement for on-chain weight consensus).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Sequence, Tuple

BPS_TOTAL = 10_000


@dataclass(frozen=True)
class ReignMember:
    """A king currently (or about to be) in the court."""

    uid: int
    hotkey: str
    model_hash: str
    submission_id: str = ""
    slot: int = 0  # 1-based position in the current reign (0 = not yet placed)


@dataclass(frozen=True)
class PlannedMember:
    member: ReignMember
    slot: int
    weight_bps: int
    is_challenger: bool = False


def challenger_beats_king(
    challenger_score: float, king_score: float, *, win_margin: float
) -> bool:
    """True iff the challenger beats the lead king by at least ``win_margin`` (absolute)."""
    return (float(challenger_score) - float(king_score)) >= float(win_margin)


def weight_bps_for_member_count(count: int) -> List[int]:
    """Even split of 10000 bps across ``count`` members (remainder to the front slots)."""
    if count < 0:
        raise ValueError("count must be >= 0")
    if count == 0:
        return []
    base = BPS_TOTAL // count
    remainder = BPS_TOTAL % count
    return [base + (1 if i < remainder else 0) for i in range(count)]


def build_reign_plan(
    active_members: Sequence[ReignMember],
    challenger: ReignMember,
    *,
    court_size: int = 5,
) -> List[PlannedMember]:
    """Coronation plan: challenger to slot 1, survivors shift down, court capped.

    The challenger's own hotkey / model_hash / submission are removed from the
    survivors so a miner cannot occupy two slots with the same model.
    """
    if court_size < 1:
        raise ValueError("court_size must be >= 1")
    ordered_active = sorted(active_members, key=lambda m: m.slot or 99)
    survivors = [
        m
        for m in ordered_active
        if m.hotkey != challenger.hotkey
        and m.model_hash != challenger.model_hash
        and (not m.submission_id or m.submission_id != challenger.submission_id)
    ]
    ordered = [challenger, *survivors[: court_size - 1]]
    bps = weight_bps_for_member_count(len(ordered))
    return [
        PlannedMember(
            member=member,
            slot=index + 1,
            weight_bps=bps[index],
            is_challenger=(index == 0),
        )
        for index, member in enumerate(ordered)
    ]


def retired_members(
    active_members: Sequence[ReignMember], planned: Sequence[PlannedMember]
) -> List[ReignMember]:
    """Members present in the old reign but not in the new plan (shifted out / replaced)."""
    kept = {(p.member.hotkey, p.member.model_hash) for p in planned}
    return [m for m in active_members if (m.hotkey, m.model_hash) not in kept]


def weight_epoch_payload(
    planned: Sequence[PlannedMember], *, burn_uid: int = 0
) -> Tuple[List[int], List[float]]:
    """(uids, normalized weights) to hand to ``set_weights``. Empty court → burn."""
    if not planned:
        return [burn_uid], [1.0]
    uids = [p.member.uid for p in planned]
    weights = [float(Decimal(p.weight_bps) / Decimal(BPS_TOTAL)) for p in planned]
    return uids, weights
