"""Reign resolution — derive the current top-5 court from chain state.

The reign is not stored in any central service: it is whatever the current on-chain
emissions say it is. A validator reads each UID's incentive from the metagraph and
joins it with that UID's committed model (from its v7 reveal) to reconstruct the
slot-ordered court. This keeps the reign fully decentralized and self-healing — a
freshly-synced validator recovers the court from chain alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from vocence.domain.spec import SubnetSpec
from vocence.ranking.koth import ReignMember


@dataclass(frozen=True)
class ChainEntry:
    """One UID's chain-derived standing."""

    uid: int
    hotkey: str
    model_hash: str
    incentive: float


def resolve_reign(entries: Sequence[ChainEntry], spec: SubnetSpec) -> List[ReignMember]:
    """Top-``court_size`` UIDs by incentive (incentive > 0) as the slot-ordered court.

    Ties break by uid so all honest validators derive the identical court.
    """
    ranked = sorted(
        (e for e in entries if e.incentive > 0),
        key=lambda e: (-e.incentive, e.uid),
    )[: spec.court_size]
    return [
        ReignMember(uid=e.uid, hotkey=e.hotkey, model_hash=e.model_hash, slot=i + 1)
        for i, e in enumerate(ranked)
    ]


def reign_from_chain(
    incentive_by_uid: Dict[int, float],
    model_by_uid: Dict[int, "tuple[str, str]"],
    spec: SubnetSpec,
) -> List[ReignMember]:
    """Build the reign from metagraph incentive + committed models.

    Args:
        incentive_by_uid: uid -> incentive (from ``metagraph.I``).
        model_by_uid: uid -> (hotkey, model_hash) from that uid's current reveal.
    """
    entries = [
        ChainEntry(uid=uid, hotkey=model_by_uid[uid][0], model_hash=model_by_uid[uid][1],
                   incentive=float(inc))
        for uid, inc in incentive_by_uid.items()
        if uid in model_by_uid
    ]
    return resolve_reign(entries, spec)
