"""
Audio assessment for Vocence using AudioJudge.

Evaluation model (v2, spec-based pointwise):
1. Extract script + voice traits from source audio via GPT-4o audio (pointwise).
2. Build task prompt from extracted traits and send to miner.
3. Extract script + voice traits from the miner's generated audio with the same pointwise call.
4. Score each element (script, gender, pitch, speed, age_group, emotion, tone, accent) against the source spec, weight, and sum to a final score in [0, 1].

Model: gemini-2.5-pro (pinned via EVAL_AUDIO_MODEL in vocence.domain.config). Gemini key only.
"""

import asyncio
import json
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from vocence.domain.config import EVAL_AUDIO_MODEL, GEMINI_AUTH_KEY
from vocence.shared.logging import emit_log


# ---------------------------------------------------------------------------
# Trait schema: closed enums the judge must pick from.
# ---------------------------------------------------------------------------

VOICE_TRAIT_ENUMS: Dict[str, List[str]] = {
    "gender":    ["male", "female", "neutral"],
    "pitch":     ["low", "mid", "high"],
    "speed":     ["slow", "normal", "fast"],
    "age_group": ["child", "young_adult", "adult", "senior"],
    "emotion":   ["neutral", "happy", "sad", "angry", "calm", "excited", "serious", "fearful"],
    "tone":      ["warm", "cold", "friendly", "formal", "casual", "authoritative"],
    "accent":    ["us", "uk", "au", "in", "neutral", "other"],
}

# Traits where "off by one position in the enum" is still partial credit.
ORDINAL_TRAITS = {"pitch", "speed", "age_group"}

# Per-element scoring weights (must sum to 1.0). Tune here to reshape scoring.
# `naturalness` is a pairwise FIRST/SECOND judge call comparing the miner's audio
# to the source audio; every other element is pointwise extraction vs the spec.
# Raw weights are renormalized so the set sums to exactly 1.0 — safe to tweak
# any single entry without manually rebalancing the rest.
_RAW_WEIGHTS: Dict[str, float] = {
    "script":      0.30,
    "naturalness": 0.15,
    "gender":      0.10,
    "speed":       0.10,
    "emotion":     0.10,
    "age_group":   0.10,
    "pitch":       0.05,
    "accent":      0.05,
    "tone":        0.05,
}
_RAW_TOTAL = sum(_RAW_WEIGHTS.values())
ELEMENT_WEIGHTS: Dict[str, float] = {k: v / _RAW_TOTAL for k, v in _RAW_WEIGHTS.items()}

# Minimum weighted score for an evaluation to count as a "pass" (generated_wins=True).
# Continuous score still drives ranking; this threshold only gates the binary counter.
PASS_THRESHOLD: float = 0.9

_FALLBACK_TRAITS: Dict[str, str] = {
    "transcription": "",
    "gender":    "neutral",
    "pitch":     "mid",
    "speed":     "normal",
    "age_group": "adult",
    "emotion":   "neutral",
    "tone":      "casual",
    "accent":    "neutral",
    # Natural-language instruction (source-side only). Empty string when not provided —
    # callers fall back to the deterministic structured form for prompt building.
    "instruction": "",
}

# Legacy / alias values we silently coerce to the new closed enums so old metadata
# and common model-drift outputs still parse.
_TRAIT_ALIASES: Dict[str, Dict[str, str]] = {
    "gender":    {"unknown": "neutral", "nonbinary": "neutral", "non_binary": "neutral"},
    "pitch":     {"normal": "mid", "medium": "mid"},
    "speed":     {"medium": "normal"},
    "age_group": {"teenager": "young_adult", "twenties": "young_adult", "thirties": "adult",
                  "forties": "adult", "fifties": "adult", "sixties": "senior", "seventies": "senior",
                  "eighties": "senior", "nineties": "senior", "unknown": "adult"},
    "emotion":   {"bored": "neutral"},
    "tone":      {"neutral": "casual"},
    "accent":    {"american": "us", "british": "uk", "australian": "au", "indian": "in",
                  "english": "uk", "unknown": "neutral"},
}

DESCRIPTION_SYSTEM = """You are an expert at analyzing speech for text-to-speech evaluation.
Analyze the audio and return a JSON object with these exact keys. For each categorical trait you MUST pick exactly one value from the listed options.

- transcription: the exact words spoken, lowercased, punctuation preserved (string)
- gender: one of [male, female, neutral]
- pitch: one of [low, mid, high]
- speed: one of [slow, normal, fast]
- age_group: one of [child, young_adult, adult, senior]
- emotion: one of [neutral, happy, sad, angry, calm, excited, serious, fearful]
- tone: one of [warm, cold, friendly, formal, casual, authoritative]
- accent: one of [us, uk, au, in, neutral, other]

Return ONLY valid JSON, no markdown, no commentary. Every value must be one of the listed options exactly as written.

Example:
{"transcription": "hello world", "gender": "male", "pitch": "mid", "speed": "normal", "age_group": "adult", "emotion": "neutral", "tone": "casual", "accent": "us"}"""


# Source-side prompt: same structured fields as above PLUS a natural-language instruction.
# The structured fields drive scoring (closed enums, deterministic comparison). The
# natural-language instruction is what we send to miners as the /speak `instruction` field
# — varied phrasing per source audio simulates real-world user prompts and prevents miners
# from overfitting to the deterministic "key: value | key: value" form.
SOURCE_DESCRIPTION_SYSTEM = """You are an expert at analyzing speech for text-to-speech evaluation.
Analyze the audio and return a JSON object with these exact keys. For each categorical trait you MUST pick exactly one value from the listed options.

- transcription: the exact words spoken, lowercased, punctuation preserved (string)
- gender: one of [male, female, neutral]
- pitch: one of [low, mid, high]
- speed: one of [slow, normal, fast]
- age_group: one of [child, young_adult, adult, senior]
- emotion: one of [neutral, happy, sad, angry, calm, excited, serious, fearful]
- tone: one of [warm, cold, friendly, formal, casual, authoritative]
- accent: one of [us, uk, au, in, neutral, other]
- instruction: a single-sentence natural-language voice prompt of the kind a real user would write for a TTS system. You MUST describe ALL SEVEN voice dimensions — gender, age_group, accent, emotion, tone, speed, AND pitch — in natural words (NOT a key:value list). Vary phrasing and word order across audios so the instruction reads like genuine user input, not a template. Do NOT include the transcription text. The instruction must be self-contained enough that a reader can infer all seven categorical traits without seeing the structured fields.

Return ONLY valid JSON, no markdown, no commentary. Every categorical value must be one of the listed options exactly as written.

Examples of acceptable instruction phrasings (each covers all 7 dimensions):
- "A cheerful, friendly young adult female speaker with a British accent, talking quickly in a high-pitched voice and a warm tone"
- "A calm middle-aged American man with a formal authoritative tone, speaking slowly in a deep low-pitched voice"
- "An excited senior woman, casual and friendly, talking fast in a moderately mid-pitched voice with an Australian accent"
- "Serious child speaker, neutral American accent, mid-pitched voice, normal speaking pace, cold detached tone"

Example response:
{"transcription": "hello world", "gender": "female", "pitch": "high", "speed": "fast", "age_group": "young_adult", "emotion": "happy", "tone": "friendly", "accent": "uk", "instruction": "A cheerful, friendly young adult female speaker with a British accent, talking quickly in a high-pitched voice and a warm tone"}"""


# ---------------------------------------------------------------------------
# Trait extraction (pointwise): single audio -> transcription + traits
# ---------------------------------------------------------------------------

def _get_judge():
    """Lazy-create AudioJudge with the Gemini key from config (GEMINI_API_KEY or GOOGLE_API_KEY in .env).

    AudioJudge routes by model name: an `EVAL_AUDIO_MODEL` containing "gemini" uses the
    Gemini backend for both pointwise and pairwise calls. OpenAI is no longer used.
    """
    from audiojudge import AudioJudge
    return AudioJudge(openai_api_key=None, google_api_key=GEMINI_AUTH_KEY)


def _normalize_trait_value(key: str, value: Any) -> str:
    """Coerce a raw trait value to the closed enum; fall back to the default if unknown."""
    if value is None:
        return _FALLBACK_TRAITS[key]
    v = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = _TRAIT_ALIASES.get(key, {})
    v = aliases.get(v, v)
    return v if v in VOICE_TRAIT_ENUMS[key] else _FALLBACK_TRAITS[key]


def _parse_traits_response(text: str) -> Dict[str, Any]:
    """Parse JSON from pointwise response; coerce every trait to its closed enum.

    Also extracts the optional `instruction` field (natural-language voice description,
    only returned by the source-side prompt). Empty string when absent.
    """
    raw = (text or "").strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break
    parsed: Dict[str, Any]
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}

    out: Dict[str, Any] = {"transcription": str(parsed.get("transcription") or "").strip()}
    for key in VOICE_TRAIT_ENUMS:
        out[key] = _normalize_trait_value(key, parsed.get(key))
    out["instruction"] = str(parsed.get("instruction") or "").strip()
    return out


async def _judge_audio_pointwise(
    audio_path: str,
    system_prompt: str = DESCRIPTION_SYSTEM,
) -> Optional[Dict[str, Any]]:
    """Run the GPT-audio pointwise call. Return parsed traits, or None if the call
    raised, returned success=False, or yielded an empty response.

    `system_prompt` selects which schema to ask for — DESCRIPTION_SYSTEM for the
    per-miner structured-only call (used in scoring), SOURCE_DESCRIPTION_SYSTEM for
    the source-audio call that also returns a natural-language `instruction`.

    Callers decide whether None means "skip the round" (source-side use) or "fall back
    to neutral defaults" (per-miner scoring use).
    """
    judge = _get_judge()
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: judge.judge_audio_pointwise(
                audio_path=audio_path,
                system_prompt=system_prompt,
                user_prompt=None,
                model=EVAL_AUDIO_MODEL,
                concatenation_method="no_concatenation",
                temperature=0.0,
                max_tokens=500,
            ),
        )
    except Exception as e:
        emit_log(f"OpenAI/AudioJudge pointwise call raised ({e})", "warn")
        return None
    if not result.get("success"):
        err = (result.get("error") or "")[:200]
        emit_log(f"OpenAI/AudioJudge pointwise returned success=False: {err}", "warn")
        return None
    response_text = (result.get("response") or "").strip()
    if not response_text:
        emit_log("OpenAI/AudioJudge pointwise returned empty response", "warn")
        return None
    return _parse_traits_response(response_text)


async def try_extract_source_traits_async(judge_client: Any, audio_path: str) -> Optional[Dict[str, Any]]:
    """Strict source-audio trait extraction for round-driving prompts.

    Uses SOURCE_DESCRIPTION_SYSTEM so the result includes both the structured 8-field
    schema (used by the scoring rubric) and a natural-language `instruction` field
    (sent to miners as the /speak `instruction` value — varied phrasing per audio).

    Returns the parsed trait dict on success, or **None** on any failure mode that
    would leave us without a usable task spec:
      - the OpenAI call raised (network down, auth bad)
      - the OpenAI call returned success=False
      - the response was empty
      - the response parsed but yielded an empty transcription (nothing for miners to say)

    Callers MUST treat None as "abort the round" — do not call miners, do not submit
    evaluation data. The validator should wait for the next sample slot and retry.
    """
    traits = await _judge_audio_pointwise(audio_path, system_prompt=SOURCE_DESCRIPTION_SYSTEM)
    if traits is None:
        return None
    if not (traits.get("transcription") or "").strip():
        emit_log("Source trait extraction yielded empty transcription; aborting round", "warn")
        return None
    return traits


async def get_transcription_and_traits_async(judge_client: Any, audio_path: str) -> Dict[str, Any]:
    """Lenient trait extraction for per-miner scoring.

    Returns the parsed traits on success, or _FALLBACK_TRAITS (neutral defaults +
    empty transcription) on any failure. Used inside score_miner_against_spec_async
    where partial credit is acceptable. For source-audio extraction that gates the
    whole round, use try_extract_source_traits_async instead.
    """
    traits = await _judge_audio_pointwise(audio_path)
    if traits is None:
        return _parse_traits_response("")
    return traits


def format_task_prompt_for_tts(traits: Dict[str, Any]) -> str:
    """Format transcription + voice description as the miner-facing task string.

    Layout: "<transcription> | <voice description>".

    When the traits dict carries a natural-language `instruction` field (source-side
    extraction via SOURCE_DESCRIPTION_SYSTEM), that's used verbatim — varied phrasing
    per source audio. Falls back to the deterministic structured form
    "gender: x | pitch: y | ..." only when the natural-language version is missing
    (per-miner extraction, fallback traits, legacy metadata).
    """
    text = (traits.get("transcription") or "").strip()
    instruction = (traits.get("instruction") or "").strip()
    if not instruction:
        parts = []
        for key in ("gender", "pitch", "speed", "age_group", "emotion", "tone", "accent"):
            v = traits.get(key)
            if v:
                parts.append(f"{key}: {v}")
        instruction = " | ".join(parts)
    return " | ".join(p for p in (text, instruction) if p)


# ---------------------------------------------------------------------------
# Per-element scoring
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein-based WER over whitespace-stripped word tokens. Clamped to [0, 1]."""
    ref = _tokenize(reference)
    hyp = _tokenize(hypothesis)
    if not ref:
        return 1.0 if hyp else 0.0
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return min(1.0, prev[m] / n)


def score_element(key: str, expected: Any, actual: Any) -> float:
    """Return a 0..1 match score for a single scoring element."""
    if key == "script":
        return max(0.0, 1.0 - word_error_rate(str(expected or ""), str(actual or "")))
    enum = VOICE_TRAIT_ENUMS.get(key)
    if not enum:
        return 1.0 if expected == actual else 0.0
    if key in ORDINAL_TRAITS:
        try:
            i = enum.index(str(expected))
            j = enum.index(str(actual))
        except ValueError:
            return 0.0
        dist = abs(i - j)
        if dist == 0:
            return 1.0
        if dist == 1:
            return 0.5
        return 0.0
    return 1.0 if expected == actual else 0.0


def score_traits_against_spec(
    source_traits: Dict[str, Any],
    miner_traits: Dict[str, Any],
    naturalness: Optional[Dict[str, Any]] = None,
) -> Tuple[float, Dict[str, Dict[str, Any]]]:
    """Compute the weighted score of miner_traits vs source_traits.

    If `naturalness` is provided (from compare_naturalness_async), contributes a
    pairwise element worth ELEMENT_WEIGHTS["naturalness"]. If None, that element
    is skipped and remaining weights renormalize.

    Returns (final_score_in_0_to_1, per_element_breakdown).
    """
    breakdown: Dict[str, Dict[str, Any]] = {}
    weight_sum = 0.0
    weighted = 0.0
    for key, weight in ELEMENT_WEIGHTS.items():
        if key == "naturalness":
            if naturalness is None:
                continue
            miner_wins = bool(naturalness.get("miner_more_natural", False))
            s = 1.0 if miner_wins else 0.0
            breakdown[key] = {
                "expected": "more natural than source",
                "actual": "more natural" if miner_wins else "less natural",
                "score": s,
                "weight": weight,
                "reasoning": naturalness.get("reasoning", ""),
                "presentation_order": naturalness.get("presentation_order", ""),
            }
        else:
            source_key = "transcription" if key == "script" else key
            expected = source_traits.get(source_key)
            actual = miner_traits.get(source_key)
            s = score_element(key, expected, actual)
            breakdown[key] = {
                "expected": expected,
                "actual": actual,
                "score": round(s, 4),
                "weight": weight,
            }
        weight_sum += weight
        weighted += weight * s
    final = weighted / weight_sum if weight_sum > 0 else 0.0
    return final, breakdown


# ---------------------------------------------------------------------------
# Pairwise naturalness (source vs miner audio)
# ---------------------------------------------------------------------------

NATURALNESS_SYSTEM_TEMPLATE = """You are an audio naturalness judge. You will hear two audio clips. Both are TTS-task outputs for the same task:

{task_description}

Question: which clip sounds MORE NATURAL as human speech? Consider clarity, prosody, intonation, and absence of robotic or synthetic artifacts. Do not consider content correctness — only naturalness.

Respond with exactly one word on the first line: FIRST or SECOND
- FIRST = the first clip is more natural
- SECOND = the second clip is more natural
Optionally add a short reason on the next line."""


async def compare_naturalness_async(
    judge_client: Any,
    source_audio_path: str,
    miner_audio_path: str,
    task_description: str,
) -> Dict[str, Any]:
    """Pairwise judge: does the miner audio sound more natural than the source?

    Randomizes presentation order to neutralize first/second position bias.

    Returns:
        {"miner_more_natural": bool, "reasoning": str, "presentation_order": str}
    """
    judge = _get_judge()
    swap = random.choice([True, False])
    if swap:
        first_path, second_path = miner_audio_path, source_audio_path
        miner_is = "first"
    else:
        first_path, second_path = source_audio_path, miner_audio_path
        miner_is = "second"

    system_prompt = NATURALNESS_SYSTEM_TEMPLATE.format(task_description=task_description or "")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: judge.judge_audio(
                audio1_path=first_path,
                audio2_path=second_path,
                system_prompt=system_prompt,
                user_prompt=None,
                model=EVAL_AUDIO_MODEL,
                concatenation_method="no_concatenation",
                temperature=0.0,
                max_tokens=200,
            ),
        )
    except Exception as e:
        emit_log(f"Naturalness pairwise failed ({e}), counting as loss", "warn")
        return {
            "miner_more_natural": False,
            "reasoning": f"error: {str(e)[:160]}",
            "presentation_order": f"{'miner' if swap else 'source'} first",
        }

    if not result.get("success"):
        return {
            "miner_more_natural": False,
            "reasoning": (result.get("error") or "judge call failed")[:160],
            "presentation_order": f"{'miner' if swap else 'source'} first",
        }

    response_text = (result.get("response") or "").strip()
    first_line = response_text.split("\n", 1)[0].strip().upper() if response_text else ""
    reasoning = response_text.split("\n", 1)[1].strip() if "\n" in response_text else ""
    winner_first = "FIRST" in first_line
    miner_wins = (winner_first and miner_is == "first") or (not winner_first and miner_is == "second")
    return {
        "miner_more_natural": miner_wins,
        "reasoning": (reasoning or first_line)[:200],
        "presentation_order": f"{'miner' if swap else 'source'} first",
    }


async def score_miner_against_spec_async(
    judge_client: Any,
    miner_audio_path: str,
    source_traits: Dict[str, Any],
    source_audio_path: Optional[str] = None,
    task_description: str = "",
) -> Dict[str, Any]:
    """Extract traits from miner audio and score them against the source spec,
    plus a pairwise naturalness comparison (miner vs source) when source_audio_path
    is provided. Extraction and naturalness calls run in parallel per miner.

    Returns:
        {
          "score": float in [0, 1],
          "generated_wins": bool (score >= PASS_THRESHOLD, kept for back-compat readers),
          "confidence": int (score * 100, rounded),
          "reasoning": short textual summary,
          "breakdown": {element: {...}, ...},
          "extracted_traits": raw traits pulled from miner audio,
          "naturalness": {"miner_more_natural": bool, ...} or None,
          "original_artifacts": [],
          "generated_artifacts": [],
        }
    """
    extract_coro = get_transcription_and_traits_async(judge_client, miner_audio_path)
    if source_audio_path:
        naturalness_coro = compare_naturalness_async(
            judge_client, source_audio_path, miner_audio_path, task_description
        )
        miner_traits, naturalness = await asyncio.gather(extract_coro, naturalness_coro)
    else:
        miner_traits = await extract_coro
        naturalness = None

    final_score, breakdown = score_traits_against_spec(source_traits, miner_traits, naturalness)

    failed = [k for k, v in breakdown.items() if v["score"] < 0.5]
    reasoning = (
        f"weighted={final_score:.3f}; "
        + ("all elements ≥0.5" if not failed else f"weak={','.join(failed)}")
    )

    return {
        "score": round(final_score, 4),
        "generated_wins": final_score >= PASS_THRESHOLD,
        "confidence": int(round(final_score * 100)),
        "reasoning": reasoning,
        "breakdown": breakdown,
        "extracted_traits": miner_traits,
        "naturalness": naturalness,
        "original_artifacts": [],
        "generated_artifacts": [],
    }


# ---------------------------------------------------------------------------
# Back-compat shims
# ---------------------------------------------------------------------------

async def generate_description_async(judge_client: Any, audio_path: str) -> str:
    """Get a TTS task prompt from one full audio (transcription + traits)."""
    traits = await get_transcription_and_traits_async(judge_client, audio_path)
    return format_task_prompt_for_tts(traits)


async def forced_choice_assessment_async(
    judge_client: Any,
    original_audio_path: str,
    generated_audio_path: str,
    task_prompt: str,
) -> Dict[str, Any]:
    """Spec-based scorer kept under the legacy name for back-compat.

    Re-extracts source traits from original_audio_path, then scores the miner audio
    against that spec. Prefer calling score_miner_against_spec_async directly with
    a pre-extracted source_traits dict to avoid the redundant source extraction.
    """
    source_traits = await get_transcription_and_traits_async(judge_client, original_audio_path)
    result = await score_miner_against_spec_async(
        judge_client,
        generated_audio_path,
        source_traits,
        source_audio_path=original_audio_path,
        task_description=task_prompt or format_task_prompt_for_tts(source_traits),
    )
    # Preserve the original/generated win shape expected by old callers.
    won = result["generated_wins"]
    naturalness = result.get("naturalness") or {}
    return {
        "original_won": not won,
        "generated_won": won,
        "score": result["score"],
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
        "breakdown": result["breakdown"],
        "extracted_traits": result["extracted_traits"],
        "naturalness": result.get("naturalness"),
        "presentation_order": naturalness.get("presentation_order", "pointwise"),
        "original_artifacts": [],
        "generated_artifacts": [],
    }
