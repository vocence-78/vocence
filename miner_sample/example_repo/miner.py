"""
Vocence TTS engine: Qwen3 12Hz checkpoint in the HF repo snapshot.

The chute snapshot is the only weight source: nothing is pulled from an external
model id at inference time. Optional vocence_config.yaml tweaks device, dtype,
attention, and language defaults.

Model load: Miner.__init__ -> _instantiate_qwen() -> Qwen3TTSModel.from_pretrained(repo_path).

Contract (Vocence):
  Miner(path_hf_repo: Path)
  warmup() -> None
  generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping

import numpy as np

_CONFIG_NAME = "config.json"
_VOCENCE_YAML = "vocence_config.yaml"


def _merge_vocence_yaml(repo: Path) -> dict[str, Any]:
    path = repo / _VOCENCE_YAML
    if not path.is_file():
        return {}
    from yaml import safe_load

    with path.open("r", encoding="utf-8") as fh:
        data = safe_load(fh)
    return data if isinstance(data, Mapping) else {}


def _ensure_repo_checkpoint(repo: Path) -> Path:
    repo = repo.resolve()
    marker = repo / _CONFIG_NAME
    if not marker.is_file():
        raise FileNotFoundError(
            f"Model snapshot incomplete: {marker} missing. "
            "Host the full Qwen3-TTS weights (checkpoint + tokenizers) in this repository."
        )
    return repo


def _resolve_compute_device(prefer_cuda: bool) -> str:
    import torch

    if prefer_cuda and torch.cuda.is_available():
        return "cuda:0"
    return "cpu"


def _resolve_torch_dtype(torch, prefer_bf16: bool):
    if prefer_bf16 and torch.cuda.is_available():
        return torch.bfloat16
    return torch.float32


def _instantiate_qwen(checkpoint_dir: str, device_map: str, torch_dtype, use_flash2: bool):
    """Load Qwen3TTSModel weights from the local repo directory (HF snapshot path)."""
    from qwen_tts import Qwen3TTSModel

    attn = "flash_attention_2" if use_flash2 else "sdpa"
    common = dict(
        pretrained_model_name_or_path=checkpoint_dir,
        device_map=device_map,
        dtype=torch_dtype,
        attn_implementation=attn,
    )
    try:
        return Qwen3TTSModel.from_pretrained(**common)
    except Exception:
        common["attn_implementation"] = "sdpa"
        return Qwen3TTSModel.from_pretrained(**common)


def _to_mono_f32(segment: np.ndarray) -> np.ndarray:
    x = np.asarray(segment, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x


class Miner:
    """
    Loads the checkpoint from the Hugging Face repo directory Chutes downloaded.
    Synthesis uses natural-language instruction + text (qwen-tts API).
    """

    def __init__(self, path_hf_repo: Path) -> None:
        self._root = _ensure_repo_checkpoint(Path(path_hf_repo))
        self._cfg = _merge_vocence_yaml(self._root)
        rt = self._cfg.get("runtime") or {}
        gen = self._cfg.get("generation") or {}
        lim = self._cfg.get("limits") or {}

        self._language = str(lim.get("default_language") or rt.get("default_language", "English"))
        self._output_sr = int(gen.get("sample_rate", 24000))
        self._cap_instruction = int(lim.get("max_instruction_chars", 600))
        self._cap_text = int(lim.get("max_text_chars", 2000))

        prefer_cuda = str(rt.get("device_preference", "cuda")).lower() == "cuda"
        want_bf16 = str(rt.get("dtype", "bfloat16")).lower() == "bfloat16"
        flash = bool(rt.get("use_flash_attention_2", False))

        import torch

        device_map = _resolve_compute_device(prefer_cuda)
        torch_dtype = _resolve_torch_dtype(torch, want_bf16)
        ckpt = str(self._root)

        self._tts = _instantiate_qwen(ckpt, device_map, torch_dtype, flash)
        # Qwen3TTSModel is a thin wrapper, not nn.Module — no .eval()
        print("Qwen3-TTS checkpoint ready (loaded from repo snapshot).")

    def __repr__(self) -> str:
        return "Miner(qwen3-tts-local, local_snapshot=True)"

    def warmup(self) -> None:
        """Force one cheap synthesis on a background thread (startup SLAs)."""
        status: dict[str, object] = {"done": False, "error": None}

        def _once() -> None:
            try:
                self.generate_wav(
                    instruction="Clear, neutral delivery.",
                    text="Warmup.",
                )
                status["done"] = True
            except Exception as exc:  # noqa: BLE001 — surface to host
                status["error"] = str(exc)

        worker = threading.Thread(target=_once, daemon=True)
        worker.start()
        worker.join(timeout=180.0)
        if not status["done"]:
            raise RuntimeError(status["error"] or "warmup exceeded 180s")

    def generate_wav(self, instruction: str, text: str) -> tuple[np.ndarray, int]:
        if self._cap_instruction > 0:
            instruction = instruction[: self._cap_instruction]
        if self._cap_text > 0:
            text = text[: self._cap_text]

        # Upstream qwen-tts method name (instruct + text -> waveform).
        waves, sr = self._tts.generate_voice_design(
            text=text,
            language=self._language,
            instruct=instruction,
        )
        if not waves:
            raise ValueError("TTS generation returned no audio")
        first = waves[0]
        if first is None:
            raise ValueError("TTS generation returned empty channel")
        return _to_mono_f32(first), int(sr)
