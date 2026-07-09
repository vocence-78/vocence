"""Chain gateway — turn raw on-chain state into candidates, reign, and weight writes.

Reads every hotkey's v7 reveal commitment and the metagraph, then derives:
  * the challenger pool (validated later by the gauntlet), and
  * the current reign (top-5 by incentive joined with committed models).

The derivation is pure (unit-tested); ``BittensorChainGateway`` is the thin async
wrapper that does the actual subtensor RPC and implements the coordinator's
``ChainGateway`` protocol. No owner API is involved — everything comes from chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from vocence.adapters.chain import parse_reveal
from vocence.domain.spec import SubnetSpec
from vocence.engine.koth_cycle import Candidate
from vocence.engine.reign import UidModel, reign_from_chain
from vocence.ranking.koth import ReignMember


@dataclass(frozen=True)
class RevealInfo:
    uid: int
    hotkey: str
    repo: str
    digest: str
    block: int = 0
    king_digest: str = ""  # king this challenger targeted (stale-parent check)

    @property
    def model_hash(self) -> str:
        # Content digest is the model's identity for court/dedup matching.
        return self.digest


def latest_reveals(
    raw: Dict[int, Tuple[str, str, int]]
) -> Dict[int, RevealInfo]:
    """Parse ``uid -> (hotkey, commit_value, block)`` into valid v7 reveals only.

    Non-v7 / malformed commitments are dropped (treated as "no submission").
    """
    out: Dict[int, RevealInfo] = {}
    for uid, (hotkey, commit_value, block) in raw.items():
        parsed = parse_reveal(commit_value)
        if not parsed:
            continue
        out[uid] = RevealInfo(uid=uid, hotkey=hotkey, repo=parsed["repo"],
                              digest=parsed["digest"], block=int(block),
                              king_digest=parsed.get("king_digest", ""))
    return out


def candidates_from_reveals(reveals: Dict[int, RevealInfo]) -> List[Candidate]:
    return [
        Candidate(uid=r.uid, hotkey=r.hotkey, repo=r.repo, digest=r.digest,
                  model_hash=r.model_hash, submission_id=f"{r.uid}:{r.digest[:16]}",
                  block=r.block, parent_king_digest=r.king_digest)
        for r in reveals.values()
    ]


def drop_stale_parents(
    candidates: List[Candidate], current_king_digest: str
) -> List[Candidate]:
    """Drop challengers that declared a target king other than the current one.

    A challenger with no declared parent (legacy / genesis) is kept. After a
    coronation this drops all in-flight challengers trained against the old king at
    once, so every validator advances in the same discrete step (teutonic stale-parent).
    """
    king = (current_king_digest or "").strip().lower()
    if not king:
        return list(candidates)  # no incumbent to match against (genesis)
    return [
        c for c in candidates
        if not c.parent_king_digest or c.parent_king_digest.strip().lower() == king
    ]


def reign_from_reveals(
    reveals: Dict[int, RevealInfo],
    incentive_by_uid: Dict[int, float],
    spec: SubnetSpec,
) -> List[ReignMember]:
    model_by_uid = {
        uid: UidModel(hotkey=r.hotkey, model_hash=r.model_hash, repo=r.repo, digest=r.digest)
        for uid, r in reveals.items()
    }
    return reign_from_chain(incentive_by_uid, model_by_uid, spec)


class BittensorChainGateway:
    """Async ``ChainGateway`` backed by bittensor. Fetches one snapshot per cycle."""

    def __init__(self, wallet, *, network: str, netuid: int, spec: SubnetSpec):
        self.wallet = wallet
        self.network = network
        self.netuid = netuid
        self.spec = spec
        self._subtensor = None
        self._reveals: Dict[int, RevealInfo] = {}
        self._incentive: Dict[int, float] = {}

    async def _st(self):  # pragma: no cover - needs chain
        if self._subtensor is None:
            import bittensor as bt
            self._subtensor = bt.AsyncSubtensor(network=self.network)
        return self._subtensor

    async def current_block(self) -> int:  # pragma: no cover - needs chain
        st = await self._st()
        return int(await st.get_current_block())

    async def _refresh(self) -> None:  # pragma: no cover - needs chain
        st = await self._st()
        metagraph = await st.metagraph(self.netuid)
        hotkeys = list(getattr(metagraph, "hotkeys", []) or [])
        incentive = list(getattr(metagraph, "I", []) or [])
        self._incentive = {uid: float(incentive[uid]) for uid in range(len(hotkeys)) if uid < len(incentive)}
        raw: Dict[int, Tuple[str, str, int]] = {}
        for uid, hotkey in enumerate(hotkeys):
            commit = await st.get_commitment(self.netuid, uid)  # v7 reveal string or ""
            raw[uid] = (hotkey, commit or "", 0)
        self._reveals = latest_reveals(raw)

    async def resolve_reign(self) -> List[ReignMember]:  # pragma: no cover - needs chain
        await self._refresh()
        return reign_from_reveals(self._reveals, self._incentive, self.spec)

    async def list_candidates(self) -> List[Candidate]:  # pragma: no cover - needs chain
        if not self._reveals:
            await self._refresh()
        return candidates_from_reveals(self._reveals)

    async def set_weights(self, uids: List[int], weights: List[float]) -> bool:  # pragma: no cover - needs chain
        st = await self._st()
        result = await st.set_weights(
            wallet=self.wallet, netuid=self.netuid, uids=uids, weights=weights,
            wait_for_inclusion=False, wait_for_finalization=False,
        )
        return bool(result[0]) if isinstance(result, tuple) else bool(result)
