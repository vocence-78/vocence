"""Dense, multi-facet, pairwise duel aggregation — the evaluation core.

A challenger is scored against the reigning slot-1 king over the fixed corpus. Each
sample yields, for each facet, a score in [0, 1] for both sides:

* **intelligibility** — Whisper WER mapped to a score, plus a hard gate.
* **adherence** — dense trait checklist answered by an open audio-LLM (the PromptTTS
  differentiator).
* **naturalness** — SpeechJudge-GRM pairwise preference.

Per-facet means are combined into a per-side weighted composite (weights from
``vocence.toml``). The challenger crowns iff it passes the intelligibility gate and
its composite beats the king's by ``win_margin``. This module is pure and
deterministic given per-sample judge outputs, so the coronation decision is
reproducible across validators (see whitepaper §6.5, §7.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Dict, List, Optional, Sequence

from vocence.domain.spec import SubnetSpec

FACETS = ("intelligibility", "adherence", "naturalness")


@dataclass(frozen=True)
class FacetPair:
    """Per-sample per-facet scores in [0, 1] for both sides."""

    king: float
    challenger: float


@dataclass(frozen=True)
class SampleRecord:
    sample_id: str
    intelligibility: FacetPair
    adherence: FacetPair
    naturalness: FacetPair
    king_intelligible: bool
    challenger_intelligible: bool
    scored: bool = True

    def facet(self, name: str) -> FacetPair:
        return getattr(self, name)


@dataclass(frozen=True)
class FacetSummary:
    king_mean: float
    challenger_mean: float
    challenger_win_rate: float  # fraction of samples where challenger > king (ties=0.5)


@dataclass(frozen=True)
class DuelResult:
    state: str  # "succeeded" | "failed"
    composite_king: Optional[float]
    composite_challenger: Optional[float]
    challenger_won: Optional[bool]
    facets: Dict[str, FacetSummary] = field(default_factory=dict)
    scored_samples: int = 0
    total_samples: int = 0
    challenger_gate_pass_rate: float = 0.0
    win_margin: float = 0.0
    reason: str = ""


def _win_rate(records: Sequence[SampleRecord], facet: str) -> float:
    wins = 0.0
    for r in records:
        p = r.facet(facet)
        if p.challenger > p.king:
            wins += 1.0
        elif p.challenger == p.king:
            wins += 0.5
    return round(wins / len(records), 6) if records else 0.0


def aggregate_duel(
    records: Sequence[SampleRecord],
    spec: SubnetSpec,
    *,
    min_valid_fraction: float = 0.8,
    gate_min_pass_rate: float = 0.9,
) -> DuelResult:
    """Aggregate per-sample judge outputs into a coronation decision.

    * Fails (state="failed") if fewer than ``min_valid_fraction`` of samples scored.
    * The challenger is disqualified (challenger_won=False) if it clears the
      intelligibility gate on fewer than ``gate_min_pass_rate`` of scored samples,
      regardless of its composite — beautiful-but-unintelligible audio cannot win.
    * Otherwise challenger_won iff composite_challenger - composite_king >= win_margin.
    """
    total = len(records)
    valid = [r for r in records if r.scored]
    if total == 0 or (valid and len(valid) / total < min_valid_fraction) or not valid:
        return DuelResult(
            state="failed", composite_king=None, composite_challenger=None,
            challenger_won=None, scored_samples=len(valid), total_samples=total,
            win_margin=spec.win_margin,
            reason=f"only {len(valid)}/{total} samples scored (< {min_valid_fraction:.0%})",
        )

    facets: Dict[str, FacetSummary] = {}
    comp_king = 0.0
    comp_chal = 0.0
    for name in FACETS:
        king_mean = round(mean(r.facet(name).king for r in valid), 6)
        chal_mean = round(mean(r.facet(name).challenger for r in valid), 6)
        facets[name] = FacetSummary(king_mean, chal_mean, _win_rate(valid, name))
        w = spec.facet_weight(name)
        comp_king += w * king_mean
        comp_chal += w * chal_mean

    comp_king = round(comp_king, 6)
    comp_chal = round(comp_chal, 6)

    gate_pass_rate = round(mean(1.0 if r.challenger_intelligible else 0.0 for r in valid), 6)
    gated_out = gate_pass_rate < gate_min_pass_rate

    won = (not gated_out) and ((comp_chal - comp_king) >= spec.win_margin)
    reason = "intelligibility_gate_failed" if gated_out else ""

    return DuelResult(
        state="succeeded",
        composite_king=comp_king,
        composite_challenger=comp_chal,
        challenger_won=won,
        facets=facets,
        scored_samples=len(valid),
        total_samples=total,
        challenger_gate_pass_rate=gate_pass_rate,
        win_margin=spec.win_margin,
        reason=reason,
    )
