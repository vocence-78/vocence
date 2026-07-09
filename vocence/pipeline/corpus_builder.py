"""Deterministic eval-corpus generator.

Produces the fixed (target_text, trait_instruction) prompts every validator evaluates.
Generation is fully deterministic (index-driven, no randomness) so anyone can rebuild
the exact corpus and verify its pinned hash. Traits are drawn from a fixed bank and
combined by index to cover the PromptTTS trait space (gender, age, tone, emotion,
pace, accent, environment) evenly across samples.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

SENTENCES: List[str] = [
    "The old lighthouse stood watch over the restless grey sea.",
    "Please remember to water the plants before you leave for the weekend.",
    "Breaking news: the city council approved the new transit plan tonight.",
    "I never expected to find such a quiet corner in the middle of the market.",
    "Could you tell me the quickest way to the central train station?",
    "The recipe calls for two cups of flour, a pinch of salt, and patience.",
    "After the storm passed, the whole valley smelled of rain and pine.",
    "Ladies and gentlemen, please fasten your seatbelts for departure.",
    "She whispered the answer so no one else in the library could hear.",
    "Our quarterly results exceeded every forecast the analysts had made.",
    "Once upon a time, in a kingdom hidden behind the mountains, magic was real.",
    "Warning: the surface may be hot; handle the tray with care.",
    "He laughed so hard at the joke that he nearly dropped his coffee.",
    "The museum opens at nine and the guided tour begins on the hour.",
    "Take a deep breath, relax your shoulders, and let the tension go.",
    "The championship comes down to this final, decisive penalty kick.",
]

TRAITS: Dict[str, List[str]] = {
    "gender": ["male", "female"],
    "age": ["child", "young adult", "middle-aged adult", "elderly person"],
    "tone": ["warm", "authoritative", "playful", "serious", "soothing"],
    "emotion": ["neutral", "happy", "sad", "angry", "fearful", "surprised"],
    "pace": ["slow", "moderate", "fast"],
    "accent": ["American", "British", "Australian", "Indian"],
    "environment": ["a quiet studio", "a busy street", "a large hall", "over a phone line"],
}
_TRAIT_KEYS = list(TRAITS.keys())


def _traits_for(i: int) -> Dict[str, str]:
    """Pick one value per trait by index.

    Stride 1 per trait guarantees every value is covered as ``i`` increases; a per-trait
    phase offset (``k*7``) decorrelates the traits so combinations still vary.
    """
    out: Dict[str, str] = {}
    for k, key in enumerate(_TRAIT_KEYS):
        values = TRAITS[key]
        out[key] = values[(i + k * 7) % len(values)]
    return out


def build_corpus(n: int) -> List[Dict[str, Any]]:
    if n < 1:
        raise ValueError("n must be >= 1")
    samples = []
    for i in range(n):
        samples.append({
            "sample_id": f"vocence-{i:05d}",
            "target_text": SENTENCES[i % len(SENTENCES)],
            "traits": _traits_for(i),
        })
    return samples


def serialize_corpus(samples: List[Dict[str, Any]]) -> bytes:
    """Canonical bytes (stable across machines) — what the pinned hash is computed over."""
    return json.dumps(samples, sort_keys=True, separators=(",", ":")).encode("utf-8")
