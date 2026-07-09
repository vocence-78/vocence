"""Caching duel runner — evaluate many challengers against a stable king cheaply.

The lead king is identical across every challenger in a reign, so its per-sample work
(generation, intelligibility, adherence) is memoized by (king digest, sample_id) and
reused for every duel. Only challenger-side facets and the pairwise naturalness
comparison are computed fresh. This roughly halves per-duel cost after the king's
first pass (see the eval-speed estimate).

Naturalness stays pairwise (it needs both audios), so it is never king-cached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.duel import CorpusSample, GenerateFn
from vocence.pipeline.dense_scoring import FacetPair, SampleRecord, DuelResult, aggregate_duel
from vocence.pipeline.judges.whisper_gate import WhisperIntelligibilityJudge
from vocence.pipeline.judges.adherence_checklist import AdherenceChecklistJudge
from vocence.pipeline.judges.speechjudge import SpeechJudgeNaturalness


@dataclass(frozen=True)
class SideResult:
    """One model's per-sample audio + non-pairwise facet scores."""

    audio: bytes
    intelligibility: float
    intelligible: bool
    adherence: float


@dataclass
class DuelRunner:
    intelligibility: WhisperIntelligibilityJudge
    adherence: AdherenceChecklistJudge
    naturalness: SpeechJudgeNaturalness
    spec: SubnetSpec
    king_cache: Dict[Tuple[str, str], SideResult] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0

    def _side(self, sample: CorpusSample, audio: bytes) -> SideResult:
        score, _wer, ok = self.intelligibility.score_side(sample.target_text, audio)
        adh = self.adherence.score_side(sample.traits, audio)
        return SideResult(audio=audio, intelligibility=score, intelligible=ok, adherence=adh)

    def _king_side(self, sample: CorpusSample, king_gen: GenerateFn, king_digest: str) -> SideResult:
        key = (king_digest, sample.sample_id)
        cached = self.king_cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.cache_misses += 1
        result = self._side(sample, king_gen(sample.target_text, sample.traits))
        self.king_cache[key] = result
        return result

    def evict_king(self, king_digest: str) -> int:
        keys = [k for k in self.king_cache if k[0] == king_digest]
        for k in keys:
            del self.king_cache[k]
        return len(keys)

    def run(
        self,
        samples: Sequence[CorpusSample],
        king_gen: GenerateFn,
        king_digest: str,
        challenger_gen: GenerateFn,
        *,
        on_error=None,
    ) -> DuelResult:
        records = []
        for sample in samples:
            try:
                ks = self._king_side(sample, king_gen, king_digest)          # cached across duels
                cs = self._side(sample, challenger_gen(sample.target_text, sample.traits))
                nat = self.naturalness.score_pair(sample.target_text, ks.audio, cs.audio)
                records.append(SampleRecord(
                    sample_id=sample.sample_id,
                    intelligibility=FacetPair(king=ks.intelligibility, challenger=cs.intelligibility),
                    adherence=FacetPair(king=ks.adherence, challenger=cs.adherence),
                    naturalness=nat,
                    king_intelligible=ks.intelligible,
                    challenger_intelligible=cs.intelligible,
                    scored=True,
                ))
            except Exception as exc:
                if on_error is not None:
                    on_error(sample.sample_id, exc)
                zero = FacetPair(0.0, 0.0)
                records.append(SampleRecord(sample.sample_id, zero, zero, zero, False, False, scored=False))
        return aggregate_duel(records, self.spec)
