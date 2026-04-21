#!/usr/bin/env python3
"""
Standalone AudioJudge exercise: extract strict JSON audio characteristics from one audio clip.

Dependencies (no .env — edit CONFIG below):
    pip install "audiojudge>=0.1.0" "openai>=1.0.0"

Usage:
    python audio_judge_single_audio_comprehensive.py /path/to/a.wav
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Edit everything here — no environment variables or dotenv.
# ---------------------------------------------------------------------------

CONFIG: dict[str, Any] = {
    # Required for OpenAI audio models
    "OPENAI_API_KEY": "sk-your-key-here",
    "GPT_AUDIO_MODEL": "gpt-4o-audio-preview",
    # Fingerprint test runs — API still costs money
    "DISABLE_CACHE": True,
    "TEMP_DIR": "temp_audio_judge_script",
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are an expert audio analyzer for speech datasets.

You will be given an audio clip.

Your task is to extract accurate voice characteristics and a transcription.

---

## TASKS

### 1. TRANSCRIPTION
Transcribe the speech exactly as spoken.
Do not paraphrase.

---

### 2. AUDIO QUALITY
Evaluate:
- clarity (clear / slightly noisy / noisy)
- noise type (none / background / music / distortion)
- speaker count (single / multiple)

---

### 3. VOICE TRAITS (STRUCTURED)

Extract the following traits:

- pitch: low / medium / high
- speaking_rate: slow / medium / fast
- energy: low / medium / high

- gender: male / female / unknown
- age_group: tenenager/twenties/thirties/fourties/fifties/sixties/seventies/eighties/nineties/unknown

- emotion: choose best match:
  calm / happy / sad / angry / fearful / neutral / excited / serious

- timbre (list 1-4 max):
  choose from:
  breathy, raspy, harsh, soft, nasal, clear, rough, warm, thin, deep, sharp

- accent: us/england/australia/canada/india/newzealand/south africa/south korea/japan/china/

- tone_style (short phrase) / voice style label:
  pick the closest match to one of the examples below.
  If none match well, output your best-effort voice style as a short phrase (1–3 words).
  Examples:
  warrior, anime, army commander, cartoon style, cinematic narrator, documentary narrator,
  news anchor, sportscaster, storyteller, audiobook narrator, podcast host, radio DJ,
  commercial voiceover, trailer voice, motivational coach, teacher, customer support,
  friendly assistant, corporate presenter, formal announcer, conversational, casual,
  playful, humorous, sarcastic, deadpan, serious, authoritative, commanding, inspirational,
  empathetic, comforting, warm, soothing, calm, whispery, intimate, seductive, mysterious,
  dramatic, theatrical, epic, heroic, villainous, menacing, monster-like, robotic, synthetic,
  childlike, elderly, aristocratic, street style, gritty, noir, fantasy mage, sci-fi captain

---

### 4. CONFIDENCE

For each category, provide confidence score (0.0-1.0)

---

## OUTPUT FORMAT (STRICT JSON)

Return ONLY JSON:

{
  "transcript": "...",

  "audio_quality": {
    "clarity": "...",
    "noise_type": "...",
    "speaker_count": "...",
    "confidence": 0.0
  },

  "voice_traits": {
    "pitch": "...",
    "pitch_confidence": 0.0,

    "speaking_rate": "...",
    "speaking_rate_confidence": 0.0,

    "energy": "...",
    "energy_confidence": 0.0,

    "gender": "...",
    "gender_confidence": 0.0,

    "age_group": "...",
    "age_group_confidence": 0.0,

    "emotion": "...",
    "emotion_confidence": 0.0,

    "timbre": ["...", "..."],
    "timbre_confidence": 0.0,

    "accent": "...",
    "accent_confidence": 0.0,

    "tone_style": "...",
    "tone_style_confidence": 0.0
  }
}
"""

def _parse_extraction_response(text: str) -> dict[str, Any]:
    """
    Parse the strict JSON schema you requested.
    Falls back to a safe default shape if the model returns invalid JSON.
    """
    defaults: dict[str, Any] = {
        "transcript": "",
        "audio_quality": {
            "clarity": "slightly noisy",
            "noise_type": "background",
            "speaker_count": "single",
            "confidence": 0.0,
        },
        "voice_traits": {
            "pitch": "medium",
            "pitch_confidence": 0.0,
            "speaking_rate": "medium",
            "speaking_rate_confidence": 0.0,
            "energy": "medium",
            "energy_confidence": 0.0,
            "gender": "unknown",
            "gender_confidence": 0.0,
            "age_group": "unknown",
            "age_group_confidence": 0.0,
            "emotion": "neutral",
            "emotion_confidence": 0.0,
            "timbre": ["clear"],
            "timbre_confidence": 0.0,
            "accent": "unknown",
            "accent_confidence": 0.0,
            "tone_style": "neutral",
            "tone_style_confidence": 0.0,
        },
    }

    if not text:
        return defaults

    raw = text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    def _clamp_confidence(x: Any) -> float:
        try:
            f = float(x)
        except (TypeError, ValueError):
            return 0.0
        if f < 0.0:
            return 0.0
        if f > 1.0:
            return 1.0
        return f

    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise json.JSONDecodeError("not an object", raw, 0)

        # Deep-merge with defaults so we always return the full strict shape.
        merged: dict[str, Any] = {
            **defaults,
            "audio_quality": {**defaults["audio_quality"]},
            "voice_traits": {**defaults["voice_traits"]},
        }

        if isinstance(obj.get("transcript"), str):
            merged["transcript"] = obj["transcript"]

        aq = obj.get("audio_quality")
        if isinstance(aq, dict):
            if isinstance(aq.get("clarity"), str):
                merged["audio_quality"]["clarity"] = aq["clarity"]
            if isinstance(aq.get("noise_type"), str):
                merged["audio_quality"]["noise_type"] = aq["noise_type"]
            if isinstance(aq.get("speaker_count"), str):
                merged["audio_quality"]["speaker_count"] = aq["speaker_count"]
            merged["audio_quality"]["confidence"] = _clamp_confidence(
                aq.get("confidence", 0.0)
            )

        vt = obj.get("voice_traits")
        if isinstance(vt, dict):
            for k in (
                "pitch",
                "speaking_rate",
                "energy",
                "gender",
                "age_group",
                "emotion",
                "accent",
                "tone_style",
            ):
                if isinstance(vt.get(k), str):
                    merged["voice_traits"][k] = vt[k]

            merged["voice_traits"]["pitch_confidence"] = _clamp_confidence(
                vt.get("pitch_confidence", 0.0)
            )
            merged["voice_traits"]["speaking_rate_confidence"] = _clamp_confidence(
                vt.get("speaking_rate_confidence", 0.0)
            )
            merged["voice_traits"]["energy_confidence"] = _clamp_confidence(
                vt.get("energy_confidence", 0.0)
            )
            merged["voice_traits"]["emotion_confidence"] = _clamp_confidence(
                vt.get("emotion_confidence", 0.0)
            )
            merged["voice_traits"]["accent_confidence"] = _clamp_confidence(
                vt.get("accent_confidence", 0.0)
            )
            merged["voice_traits"]["gender_confidence"] = _clamp_confidence(
                vt.get("gender_confidence", 0.0)
            )

            merged["voice_traits"]["age_group_confidence"] = _clamp_confidence(
                vt.get("age_group_confidence", 0.0)
            )

            timbre = vt.get("timbre")
            if isinstance(timbre, list):
                merged["voice_traits"]["timbre"] = [t for t in timbre if isinstance(t, str)][:4] or defaults["voice_traits"]["timbre"]
            elif isinstance(timbre, str):
                merged["voice_traits"]["timbre"] = [timbre]
            merged["voice_traits"]["timbre_confidence"] = _clamp_confidence(
                vt.get("timbre_confidence", 0.0)
            )

            merged["voice_traits"]["tone_style_confidence"] = _clamp_confidence(
                vt.get("tone_style_confidence", 0.0)
            )

        return merged
    except json.JSONDecodeError:
        pass

    # Last resort: keep whatever text we got (but still return strict shape).
    defaults["transcript"] = raw[:500]
def run_pointwise(
    judge: Any,
    audio_path: str,
    system_prompt: str,
    *,
    user_prompt: str | None,
    max_tokens: int,
) -> dict[str, Any]:
    return judge.judge_audio_pointwise(
        audio_path=audio_path,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=CONFIG["GPT_AUDIO_MODEL"],
        concatenation_method="no_concatenation",
        temperature=0.00000001,
        max_tokens=max_tokens,
    )


def _die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AudioJudge strict JSON extraction (single audio input only)."
    )
    parser.add_argument(
        "audio_path",
        help="Path to an audio clip (wav/mp3/etc. supported by AudioJudge/OpenAI).",
    )
    args = parser.parse_args()

    api_key = ""
    if not api_key or api_key.startswith("sk-your"):
        _die("Set CONFIG['OPENAI_API_KEY'] to your real key.")

    p_path = Path(args.audio_path).expanduser()
    if not p_path.is_file():
        _die(f"Audio file not found: {p_path}")

    try:
        from audiojudge import AudioJudge
    except ImportError:
        _die('Install dependencies: pip install "audiojudge>=0.1.0" "openai>=1.0.0"')

    judge = AudioJudge(
        openai_api_key=api_key,
        google_api_key=None,
        temp_dir=str(CONFIG.get("TEMP_DIR") or "temp_audio_judge_script"),
        disable_cache=bool(CONFIG.get("DISABLE_CACHE", True)),
    )

    r1 = run_pointwise(
        judge,
        str(p_path),
        EXTRACTION_SYSTEM_PROMPT,
        user_prompt=None,
        max_tokens=500,
    )
    if not r1.get("success"):
        _die(f"Pointwise traits failed: {r1.get('error', r1)}")

    extraction = _parse_extraction_response(r1.get("response") or "")
    # IMPORTANT: output must be ONLY strict JSON.
    print(json.dumps(extraction, ensure_ascii=False))


if __name__ == "__main__":
    main()
