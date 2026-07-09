"""Genesis king — the base model as the founding incumbent.

Before any coronation the on-chain reign is empty. Rather than let the first valid
challenger take the hill unopposed, a validator seeds the court with the genesis king:
the canonical base model (``seed_repo`` @ ``seed_weights_hash`` from ``vocence.toml``),
attributed to the owner UID. A challenger must then beat the base by the win margin to
be crowned — exactly the intended "beat the base to earn emissions" rule.
"""

from __future__ import annotations

from typing import List

from vocence.domain.spec import SubnetSpec
from vocence.ranking.koth import ReignMember


def genesis_digest(spec: SubnetSpec) -> str:
    """The base model's content digest as ``sha256:<hex>`` (from the pinned seed hash)."""
    h = (spec.seed_weights_hash or "").strip().lower()
    return h if h.startswith("sha256:") else f"sha256:{h}"


def genesis_reign(spec: SubnetSpec, *, owner_uid: int, owner_hotkey: str) -> List[ReignMember]:
    """Single-member court holding the base model at slot 1."""
    digest = genesis_digest(spec)
    return [ReignMember(
        uid=owner_uid,
        hotkey=owner_hotkey,
        model_hash=digest,
        slot=1,
        repo=spec.seed_repo,
        digest=digest,
    )]
