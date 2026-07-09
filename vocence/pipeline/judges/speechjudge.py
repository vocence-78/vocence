"""Naturalness facet — SpeechJudge-GRM pairwise preference.

SpeechJudge-GRM (open, Qwen2.5-Omni-7B fine-tuned; arXiv 2511.07931) judges which of
two speech outputs is more natural given the target text — exactly the KOTH duel. It
is the only open judge that clears human-level agreement on naturalness, and objective
metrics are near-random here, so this facet is what keeps the network honest about
perceived quality.

The comparator ``(text, king_audio, challenger_audio) -> preference`` returns the
challenger's preference in {0.0, 0.5, 1.0} (1.0 = challenger clearly more natural).
Optional order-swapped voting reduces positional bias. Injectable for testing.
"""

from __future__ import annotations

from statistics import mean
from typing import Callable, Optional

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.dense_scoring import FacetPair

Comparator = Callable[[str, bytes, bytes], float]


def _snap(pref: float) -> float:
    """Snap a mean preference to the nearest ternary value {0, 0.5, 1}."""
    if pref < 1.0 / 3:
        return 0.0
    if pref > 2.0 / 3:
        return 1.0
    return 0.5


class SpeechJudgeNaturalness:
    def __init__(
        self,
        spec: SubnetSpec,
        comparator: Optional[Comparator] = None,
        model_id: Optional[str] = None,
        votes: int = 1,
    ):
        if votes < 1:
            raise ValueError("votes must be >= 1")
        self.spec = spec
        self._comparator = comparator
        self.votes = votes
        self.model_id = model_id or spec.judges.get("naturalness", "AmphionTeam/SpeechJudge-GRM")

    def _ensure(self) -> Comparator:
        if self._comparator is None:
            self._comparator = _load_speechjudge(self.model_id)
        return self._comparator

    def score_pair(self, target_text: str, king_audio: bytes, challenger_audio: bytes) -> FacetPair:
        cmp = self._ensure()
        prefs = []
        for i in range(self.votes):
            if i % 2 == 0:
                prefs.append(cmp(target_text, king_audio, challenger_audio))
            else:
                # swap order to cancel positional bias; invert the returned preference
                prefs.append(1.0 - cmp(target_text, challenger_audio, king_audio))
        pref = _snap(mean(prefs))
        return FacetPair(king=round(1.0 - pref, 6), challenger=round(pref, 6))


def _load_speechjudge(model_id: str) -> Comparator:  # pragma: no cover - GPU path
    raise RuntimeError(
        f"Naturalness judge needs SpeechJudge-GRM {model_id!r} served locally on the "
        "validator GPU. Provide a `comparator` callable or wire the local server here."
    )
