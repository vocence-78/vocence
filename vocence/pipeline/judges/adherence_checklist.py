"""Prompt-adherence facet — dense trait checklist answered by an open audio-LLM.

The primary PromptTTS differentiator. For each sample the same deterministic trait
questions (see :mod:`trait_questions`) are answered for the king and the challenger by
an open audio-LLM (Kimi-Audio / Qwen2.5-Omni); the mean ternary answer is each side's
adherence score. The answerer callable ``(audio, questions) -> [ternary]`` is
injectable for testing; when omitted the model is lazily loaded.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.dense_scoring import FacetPair
from vocence.pipeline.judges.trait_questions import build_trait_questions, aggregate_ternary

Answerer = Callable[[bytes, List[Dict[str, str]]], Sequence[float]]


class AdherenceChecklistJudge:
    def __init__(
        self,
        spec: SubnetSpec,
        answerer: Optional[Answerer] = None,
        model_id: Optional[str] = None,
    ):
        self.spec = spec
        self._answerer = answerer
        self.model_id = model_id or spec.judges.get("adherence", "moonshotai/Kimi-Audio-7B-Instruct")

    def _ensure(self) -> Answerer:
        if self._answerer is None:
            self._answerer = _load_audio_llm(self.model_id)
        return self._answerer

    def questions_for(self, traits: Dict[str, object]) -> List[Dict[str, str]]:
        return build_trait_questions(traits, self.spec.adherence_questions_per_sample)

    def score_pair(
        self, traits: Dict[str, object], king_audio: bytes, challenger_audio: bytes
    ) -> FacetPair:
        """Answer the identical question set for both sides (paired comparison)."""
        questions = self.questions_for(traits)
        answer = self._ensure()
        king = aggregate_ternary(answer(king_audio, questions))
        challenger = aggregate_ternary(answer(challenger_audio, questions))
        return FacetPair(king=king, challenger=challenger)


def _load_audio_llm(model_id: str) -> Answerer:  # pragma: no cover - GPU path
    raise RuntimeError(
        f"Adherence judge needs the open audio-LLM {model_id!r} served locally on the "
        "validator GPU (e.g. via vLLM/transformers). Provide an `answerer` callable or "
        "wire the local server here."
    )
