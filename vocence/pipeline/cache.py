"""Generation cache — reuse the king's audio across a reign.

The lead king does not change between challengers, so regenerating its audio for every
duel wastes the most expensive step. Keying generated audio by (model digest,
sample_id) lets a validator generate the king's corpus once per reign and reuse it for
every challenger, roughly halving TTS work (and enabling king-side judge caching later).
Pure and unit-testable; the wrapped generator does the real GPU work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Tuple

from vocence.pipeline.duel import GenerateFn


@dataclass
class GenerationCache:
    """(digest, sample_id) -> audio bytes, with simple hit/miss accounting."""

    store: Dict[Tuple[str, str], bytes] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def get_or_make(self, digest: str, sample_id: str, make: Callable[[], bytes]) -> bytes:
        key = (digest, sample_id)
        if key in self.store:
            self.hits += 1
            return self.store[key]
        self.misses += 1
        audio = make()
        self.store[key] = audio
        return audio

    def evict_digest(self, digest: str) -> int:
        """Drop all entries for a digest (e.g. a retired king). Returns count removed."""
        keys = [k for k in self.store if k[0] == digest]
        for k in keys:
            del self.store[k]
        return len(keys)


def cached_generator(base: GenerateFn, digest: str, cache: GenerationCache) -> GenerateFn:
    """Wrap a generator so identical (digest, sample_id) audio is generated once.

    The wrapper uses the target_text as the per-sample key when a sample_id is not
    threaded through; callers that have stable sample ids should pass them via the
    ``sample_id=`` keyword (supported below) for exact reuse.
    """

    def _gen(target_text, traits, sample_id: str | None = None):
        key = sample_id if sample_id is not None else target_text
        return cache.get_or_make(digest, key, lambda: base(target_text, traits))

    return _gen
