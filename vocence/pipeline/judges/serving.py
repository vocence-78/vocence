"""Turnkey judge backends over local OpenAI-compatible servers (e.g. vLLM).

Lets a validator point the judges at locally-served models instead of injecting Python
callables: run the audio-LLM and SpeechJudge-GRM behind vLLM (fp8 recommended) and
Whisper behind any OpenAI-compatible ``/audio/transcriptions`` endpoint. The
message-building and response-parsing are pure and unit-tested; the httpx calls that
hit the servers are thin wrappers.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

_TERNARY = (0.0, 0.5, 1.0)


@dataclass(frozen=True)
class JudgeEndpoint:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 120.0
    quantization: str = "fp8"  # advisory: how the model is served


# --------------------------------------------------------------------------- parsing (pure)
def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _snap_ternary(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(_TERNARY, key=lambda t: abs(t - x))


def parse_ternary_answers(raw: str, question_ids: Sequence[str]) -> List[float]:
    """Parse ``{"answers":[{"id","score"}]}`` into ternary floats in question order.

    Missing / unparseable answers default to 0.0 (a check not clearly satisfied fails).
    """
    obj = _extract_json(raw)
    by_id: Dict[str, float] = {}
    if isinstance(obj, dict) and isinstance(obj.get("answers"), list):
        for a in obj["answers"]:
            if isinstance(a, dict) and "id" in a:
                by_id[str(a["id"])] = _snap_ternary(a.get("score"))
    return [by_id.get(qid, 0.0) for qid in question_ids]


def parse_pairwise(raw: str) -> float:
    """Parse a naturalness verdict into the challenger's preference in {0, 0.5, 1}.

    Convention: audio A = king, audio B = challenger. ``{"winner":"A"|"B"|"tie"}``.
    """
    obj = _extract_json(raw)
    winner = str(obj.get("winner", "")).strip().lower() if isinstance(obj, dict) else ""
    if winner in ("b", "challenger"):
        return 1.0
    if winner in ("a", "king"):
        return 0.0
    return 0.5


# --------------------------------------------------------------------------- message builders (pure)
def build_answer_messages(audio_b64: str, questions: Sequence[Dict[str, str]]) -> list:
    shown = [{"id": q["id"], "text": q["text"]} for q in questions]
    system = ("You judge whether a speech clip satisfies each check about its requested voice traits. "
              "Answer every check with a score: 1 (clearly satisfied), 0.5 (partially), 0 (not/unverifiable). "
              'Return strict JSON: {"answers":[{"id":"q_01","score":1}]}. One entry per id.')
    user = [
        {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
        {"type": "text", "text": "Checks:\n" + json.dumps(shown, ensure_ascii=False)},
    ]
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_compare_messages(king_b64: str, challenger_b64: str, target_text: str) -> list:
    system = ("You compare two speech clips (A and B) reading the same text and decide which sounds more "
              "natural — prosody, pacing, articulation, overall naturalness. "
              'Return strict JSON: {"winner":"A"|"B"|"tie"}.')
    user = [
        {"type": "text", "text": f"Target text: {target_text}"},
        {"type": "text", "text": "Clip A:"},
        {"type": "input_audio", "input_audio": {"data": king_b64, "format": "wav"}},
        {"type": "text", "text": "Clip B:"},
        {"type": "input_audio", "input_audio": {"data": challenger_b64, "format": "wav"}},
    ]
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def b64(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")


# --------------------------------------------------------------------------- clients (I/O)
def _chat(ep: JudgeEndpoint, messages: list) -> str:  # pragma: no cover - network
    import httpx
    headers = {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}
    with httpx.Client(base_url=ep.base_url.rstrip("/"), timeout=ep.timeout, headers=headers) as c:
        r = c.post("/v1/chat/completions",
                   json={"model": ep.model, "messages": messages, "temperature": 0.0})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def make_adherence_answerer(ep: JudgeEndpoint):  # pragma: no cover - network
    def answerer(audio: bytes, questions):
        ids = [q["id"] for q in questions]
        raw = _chat(ep, build_answer_messages(b64(audio), questions))
        return parse_ternary_answers(raw, ids)
    return answerer


def make_speechjudge_comparator(ep: JudgeEndpoint):  # pragma: no cover - network
    def comparator(target_text: str, king_audio: bytes, challenger_audio: bytes) -> float:
        raw = _chat(ep, build_compare_messages(b64(king_audio), b64(challenger_audio), target_text))
        return parse_pairwise(raw)
    return comparator


def make_whisper_transcriber(ep: JudgeEndpoint):  # pragma: no cover - network
    import httpx

    def transcribe(audio: bytes) -> str:
        headers = {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}
        with httpx.Client(base_url=ep.base_url.rstrip("/"), timeout=ep.timeout, headers=headers) as c:
            r = c.post("/v1/audio/transcriptions",
                       files={"file": ("audio.wav", audio, "audio/wav")},
                       data={"model": ep.model})
            r.raise_for_status()
            return r.json().get("text", "")
    return transcribe
