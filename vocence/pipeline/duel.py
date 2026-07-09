"""Duel orchestrator — run a challenger against the king over the fixed corpus.

Ties the three judges to the aggregation core. For each corpus sample it generates
audio from both models (same canonical inference path, different weights), scores the
three facets, and builds a :class:`SampleRecord`; the batch is aggregated into a
:class:`DuelResult`. Generation is injected as callables so the orchestration is
testable without loading TTS models (the coordinator supplies real generators in
Phase 3). One bad sample degrades to ``scored=False`` rather than aborting the duel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.dense_scoring import (
    FacetPair, SampleRecord, DuelResult, aggregate_duel,
)
from vocence.pipeline.judges.whisper_gate import WhisperIntelligibilityJudge
from vocence.pipeline.judges.adherence_checklist import AdherenceChecklistJudge
from vocence.pipeline.judges.speechjudge import SpeechJudgeNaturalness

# (target_text, traits) -> audio bytes, for one loaded model
GenerateFn = Callable[[str, Dict[str, object]], bytes]


@dataclass(frozen=True)
class CorpusSample:
    sample_id: str
    target_text: str
    traits: Dict[str, object]


def score_sample(
    sample: CorpusSample,
    king_audio: bytes,
    challenger_audio: bytes,
    *,
    intelligibility: WhisperIntelligibilityJudge,
    adherence: AdherenceChecklistJudge,
    naturalness: SpeechJudgeNaturalness,
) -> SampleRecord:
    ii_k = intelligibility.score_side(sample.target_text, king_audio)
    ii_c = intelligibility.score_side(sample.target_text, challenger_audio)
    adh = adherence.score_pair(sample.traits, king_audio, challenger_audio)
    nat = naturalness.score_pair(sample.target_text, king_audio, challenger_audio)
    return SampleRecord(
        sample_id=sample.sample_id,
        intelligibility=FacetPair(king=ii_k[0], challenger=ii_c[0]),
        adherence=adh,
        naturalness=nat,
        king_intelligible=ii_k[2],
        challenger_intelligible=ii_c[2],
        scored=True,
    )


def _unscored(sample_id: str) -> SampleRecord:
    zero = FacetPair(0.0, 0.0)
    return SampleRecord(sample_id, zero, zero, zero, False, False, scored=False)


def run_duel(
    samples: Sequence[CorpusSample],
    king_generate: GenerateFn,
    challenger_generate: GenerateFn,
    *,
    intelligibility: WhisperIntelligibilityJudge,
    adherence: AdherenceChecklistJudge,
    naturalness: SpeechJudgeNaturalness,
    spec: SubnetSpec,
    on_error: Callable[[str, Exception], None] | None = None,
) -> DuelResult:
    records: List[SampleRecord] = []
    for sample in samples:
        try:
            k_audio = king_generate(sample.target_text, sample.traits)
            c_audio = challenger_generate(sample.target_text, sample.traits)
            records.append(score_sample(
                sample, k_audio, c_audio,
                intelligibility=intelligibility, adherence=adherence, naturalness=naturalness,
            ))
        except Exception as exc:  # one bad sample must not abort the whole duel
            if on_error is not None:
                on_error(sample.sample_id, exc)
            records.append(_unscored(sample.sample_id))
    return aggregate_duel(records, spec)
