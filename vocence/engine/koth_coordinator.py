"""KOTH validator coordinator — one full evaluation cycle, end to end.

Composes every piece into the loop a validator runs each cycle, over injectable
dependencies (chain, model fetch, TTS generation, judges) so the orchestration is
testable without a chain node or GPU. A running validator supplies real
implementations; this module owns the control flow, not the I/O.

Cycle:
  1. resolve the reign from chain (incentive + committed models);
  2. list validated challengers; pick the next one (earliest, not the lead king);
  3. genesis (empty reign) -> crown directly; otherwise duel vs the lead king;
  4. plan weights via the KOTH court and set them on chain;
  5. return a :class:`CycleReport` (also the dashboard's per-cycle record).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Sequence

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.duel import CorpusSample, GenerateFn, run_duel
from vocence.pipeline.dense_scoring import DuelResult
from vocence.pipeline.judges.whisper_gate import WhisperIntelligibilityJudge
from vocence.pipeline.judges.adherence_checklist import AdherenceChecklistJudge
from vocence.pipeline.judges.speechjudge import SpeechJudgeNaturalness
from vocence.ranking.koth import ReignMember
from vocence.engine.koth_cycle import (
    Candidate, lead_king, select_challenger, challenger_from_candidate, plan_after_duel,
)


class ChainGateway(Protocol):
    async def current_block(self) -> int: ...
    async def resolve_reign(self) -> List[ReignMember]: ...
    async def list_candidates(self) -> List[Candidate]: ...
    async def set_weights(self, uids: List[int], weights: List[float]) -> bool: ...


# validate a candidate (fetch from Hippius + run the gauntlet); returns (ok, reason)
Validator = Callable[[Candidate], Awaitable["tuple[bool, str]"]]
# build a TTS generation fn for a given (repo, digest): downloads + loads the model
GeneratorFactory = Callable[[str, str], Awaitable[GenerateFn]]


@dataclass(frozen=True)
class Judges:
    intelligibility: WhisperIntelligibilityJudge
    adherence: AdherenceChecklistJudge
    naturalness: SpeechJudgeNaturalness


@dataclass
class CycleReport:
    block: int
    reign_uids: List[int]
    challenger_uid: Optional[int]
    coronated: bool
    weights_uids: List[int]
    weights: List[float]
    duel: Optional[DuelResult] = None
    note: str = ""
    challenger_hotkey: str = ""
    challenger_repo: str = ""
    run_id: str = ""
    records: list = field(default_factory=list)  # per-sample SampleRecords (in-memory; for run-detail publishing)


async def run_cycle(
    *,
    chain: ChainGateway,
    validate: Validator,
    make_generator: GeneratorFactory,
    judges: Judges,
    corpus: Sequence[CorpusSample],
    spec: SubnetSpec,
    genesis_reign: Sequence[ReignMember] = (),
    duel_runner=None,
) -> CycleReport:
    block = await chain.current_block()
    reign = await chain.resolve_reign()
    if not reign and genesis_reign:
        # Seed the empty hill with the base model so challengers must beat it.
        reign = list(genesis_reign)
    reign_uids = [m.uid for m in reign]

    candidates = await chain.list_candidates()
    candidate = select_challenger(candidates, reign)
    if candidate is None:
        from vocence.engine.koth_cycle import current_reign_weights
        uids, weights = current_reign_weights(reign, spec)
        await chain.set_weights(uids, weights)
        return CycleReport(block, reign_uids, None, False, uids, weights, note="no_challenger")

    ok, reason = await validate(candidate)
    if not ok:
        from vocence.engine.koth_cycle import current_reign_weights
        uids, weights = current_reign_weights(reign, spec)
        await chain.set_weights(uids, weights)
        return CycleReport(block, reign_uids, candidate.uid, False, uids, weights,
                           note=f"invalid_challenger:{reason}",
                           challenger_hotkey=candidate.hotkey, challenger_repo=candidate.repo)

    challenger = challenger_from_candidate(candidate)
    king = lead_king(reign)
    run_id = f"{block}-{candidate.uid}"
    records: list = []

    if king is None:
        # Genesis: no incumbent to duel; the first valid challenger takes the hill.
        result = DuelResult(state="succeeded", composite_king=0.0, composite_challenger=1.0,
                            challenger_won=True, win_margin=spec.win_margin, reason="genesis")
    else:
        king_gen = await make_generator(king.repo, king.digest)
        chal_gen = await make_generator(candidate.repo, candidate.digest)
        if duel_runner is not None:
            # King-caching runner: king audio + king-side facets reused across challengers.
            result = duel_runner.run(corpus, king_gen, king.digest, chal_gen)
            records = list(duel_runner.last_records)
        else:
            result = run_duel(
                corpus, king_gen, chal_gen,
                intelligibility=judges.intelligibility, adherence=judges.adherence,
                naturalness=judges.naturalness, spec=spec,
            )

    uids, weights, coronated = plan_after_duel(reign, challenger, result, spec)
    await chain.set_weights(uids, weights)
    return CycleReport(block, reign_uids, candidate.uid, coronated, uids, weights, duel=result,
                       challenger_hotkey=candidate.hotkey, challenger_repo=candidate.repo,
                       run_id=run_id, records=records)
