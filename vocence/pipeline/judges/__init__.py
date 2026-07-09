"""Open, self-hostable judge adapters for the multi-facet pairwise duel.

No judge here calls a centralized/hosted API: intelligibility uses Whisper, adherence
uses an open audio-LLM over a deterministic trait checklist, naturalness uses
SpeechJudge-GRM. Each adapter keeps model inference behind an injectable callable so
the scoring logic is unit-testable without a GPU, and the model is lazily loaded only
when no callable is supplied.
"""

from vocence.pipeline.judges.trait_questions import build_trait_questions, aggregate_ternary
from vocence.pipeline.judges.whisper_gate import WhisperIntelligibilityJudge
from vocence.pipeline.judges.adherence_checklist import AdherenceChecklistJudge
from vocence.pipeline.judges.speechjudge import SpeechJudgeNaturalness

__all__ = [
    "build_trait_questions",
    "aggregate_ternary",
    "WhisperIntelligibilityJudge",
    "AdherenceChecklistJudge",
    "SpeechJudgeNaturalness",
]
