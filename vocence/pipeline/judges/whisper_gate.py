"""Intelligibility facet — Whisper WER, deterministic gate + score."""

from __future__ import annotations

from typing import Callable, Optional, Tuple

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.wer import word_error_rate, intelligibility_score


class WhisperIntelligibilityJudge:
    """Transcribes an output with the pinned Whisper model and scores it against the
    target text. ``transcriber`` (bytes -> text) is injectable for testing; when
    omitted the model is lazily loaded on first use."""

    def __init__(
        self,
        spec: SubnetSpec,
        transcriber: Optional[Callable[[bytes], str]] = None,
        model_id: Optional[str] = None,
    ):
        self.spec = spec
        self._transcriber = transcriber
        self.model_id = model_id or spec.judges.get("intelligibility", "openai/whisper-large-v3")

    def _ensure(self) -> Callable[[bytes], str]:
        if self._transcriber is None:
            self._transcriber = _load_whisper(self.model_id)
        return self._transcriber

    def transcribe(self, audio: bytes) -> str:
        return self._ensure()(audio)

    def score_side(self, target_text: str, audio: bytes) -> Tuple[float, float, bool]:
        """Return (score in [0,1], wer, intelligible) for one output."""
        hyp = self.transcribe(audio)
        wer = word_error_rate(target_text, hyp)
        return intelligibility_score(wer), wer, wer <= self.spec.intelligibility_max_wer


def _load_whisper(model_id: str) -> Callable[[bytes], str]:  # pragma: no cover - GPU path
    """Lazily build a Whisper transcriber. Requires transformers + torch on a GPU host."""
    try:
        import io
        import soundfile as sf
        from transformers import pipeline as hf_pipeline
    except ImportError as exc:
        raise RuntimeError(
            "Whisper judge needs 'transformers', 'torch', and 'soundfile' installed on the "
            f"validator GPU host to run {model_id!r} locally."
        ) from exc

    asr = hf_pipeline("automatic-speech-recognition", model=model_id.split("/")[-1]
                      if "/" not in model_id else model_id)

    def _transcribe(audio: bytes) -> str:
        data, sr = sf.read(io.BytesIO(audio))
        return asr({"array": data, "sampling_rate": sr}).get("text", "")

    return _transcribe
