"""Deterministic trait -> question bank for the dense prompt-adherence checklist.

Albedo has an evaluator LLM write the per-sample questions, which is fine when one
operator runs the eval. Vocence needs every validator to score identically, so the
questions are generated **deterministically** from the structured trait instruction
via a fixed template bank. Each requested trait expands into several self-contained
ternary questions (phrased so 1.0 = good); a fixed set of consistency questions pads
toward ``adherence_questions_per_sample``. Same seed traits -> byte-identical
question set on every validator.
"""

from __future__ import annotations

from statistics import mean
from typing import Dict, List, Sequence

# Ordered so question ids are stable regardless of dict insertion order.
TRAIT_ORDER = (
    "gender", "age", "tone", "emotion", "emotion_intensity",
    "pace", "pitch", "accent", "environment",
)

TRAIT_TEMPLATES: Dict[str, List[str]] = {
    "gender": [
        "Is the speaker's voice clearly {value}?",
        "Does the vocal timbre match a {value} speaker?",
    ],
    "age": [
        "Does the speaker sound like a {value}?",
        "Is the vocal maturity consistent with {value}?",
    ],
    "tone": [
        "Is the overall tone {value}?",
        "Does the emotional colour of the delivery read as {value}?",
    ],
    "emotion": [
        "Does the speech express {value}?",
        "Is {value} conveyed through prosody, not just word choice?",
        "Would a listener identify the emotion as {value}?",
    ],
    "emotion_intensity": [
        "Is the emotional intensity {value}?",
    ],
    "pace": [
        "Is the speaking rate {value}?",
        "Does the rhythm stay {value} across the whole clip?",
    ],
    "pitch": [
        "Is the pitch {value}?",
    ],
    "accent": [
        "Does the accent sound {value}?",
        "Is the {value} accent consistent throughout the clip?",
    ],
    "environment": [
        "Does the recording environment sound like {value}?",
        "Are the background characteristics consistent with {value}?",
    ],
}

CONSISTENCY_QUESTIONS: List[str] = [
    "Does the voice match the overall description in the instruction?",
    "Is the delivery free of contradictory trait cues?",
    "Does the speaker identity stay consistent for the entire clip?",
    "Is the requested style maintained from the first word to the last?",
    "Is the audio free of artifacts that undermine the requested style?",
]


def _norm(value) -> str:
    return str(value).strip()


def build_trait_questions(traits: Dict[str, object], max_n: int) -> List[Dict[str, str]]:
    """Deterministic ternary question set for one sample's trait instruction.

    Args:
        traits: requested trait -> value (e.g. {"gender": "female", "emotion": "calm"}).
        max_n: cap on questions (``adherence_questions_per_sample`` from the spec).

    Returns:
        Ordered ``[{"id","text","trait"}]``, ids ``q_01..`` assigned after ordering.
    """
    questions: List[Dict[str, str]] = []
    for trait in TRAIT_ORDER:
        if trait not in traits or traits[trait] in (None, ""):
            continue
        value = _norm(traits[trait])
        for template in TRAIT_TEMPLATES.get(trait, []):
            questions.append({"trait": trait, "text": template.format(value=value)})
    for q in CONSISTENCY_QUESTIONS:
        questions.append({"trait": "consistency", "text": q})

    questions = questions[:max_n]
    for i, q in enumerate(questions, 1):
        q["id"] = f"q_{i:02d}"
    return questions


def aggregate_ternary(answers: Sequence[float]) -> float:
    """Mean of ternary answers in {0.0, 0.5, 1.0}. Empty -> 0.0."""
    vals = [max(0.0, min(1.0, float(a))) for a in answers if a is not None]
    return round(mean(vals), 6) if vals else 0.0
