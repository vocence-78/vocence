"""Word/character error rate — the deterministic intelligibility signal.

Pure functions: given a reference text and a hypothesis transcript (produced by the
pinned Whisper model), compute WER/CER. Kept separate from model inference so the
scoring math is reproducible and unit-testable without a GPU.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — a stable normalization so
    honest validators compute identical error rates from the same transcript."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> List[str]:
    return normalize_text(text).split()


def _levenshtein(ref: List, hyp: List) -> int:
    """Edit distance between two token/char sequences (iterative, O(len)*O(len))."""
    if not ref:
        return len(hyp)
    if not hyp:
        return len(ref)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cost = 0 if r == h else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER = edit distance over words / #reference words. Empty reference → 0.0 if the
    hypothesis is also empty, else 1.0."""
    ref, hyp = _tokens(reference), _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = list(normalize_text(reference).replace(" ", ""))
    hyp = list(normalize_text(hypothesis).replace(" ", ""))
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def intelligibility_score(wer: float) -> float:
    """Map WER to a [0, 1] score (1.0 = perfect). Clamped."""
    return max(0.0, min(1.0, 1.0 - float(wer)))
