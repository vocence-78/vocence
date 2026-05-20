"""
Example Vocence PromptTTS engine (MOCK). Replace with your real model.

Contract: this file must be named miner.py and define class Miner with
  - __init__(path_hf_repo: Path)
  - warmup() -> None
  - generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]

Loading rule (enforced by the canonical wrapper, hash-locked):
  - Read model_name from vocence_config.yaml; it must equal what you committed on chain.
  - Call from_pretrained(model_name) — bare variable only, no string literals.
  - No snapshot_download / hf_hub_download / pipeline / torch.hub.load / requests / etc.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml


class Miner:
    """Mock PromptTTS engine: returns silence. Replace with your real model loading and synthesis."""

    def __init__(self, path_hf_repo: Path) -> None:
        self._repo_path = Path(path_hf_repo).resolve()
        with (self._repo_path / "vocence_config.yaml").open() as f:
            self._config = yaml.safe_load(f) or {}
        model_name = self._config["model_name"]
        # Real miners would load their model here, e.g.:
        #   from transformers import AutoModel, AutoProcessor
        #   self.processor = AutoProcessor.from_pretrained(model_name)
        #   self.model = AutoModel.from_pretrained(model_name)
        # The wrapper has already snapshot-downloaded model_name into the HF cache,
        # so from_pretrained loads from disk without hitting the network.
        self._model_name = model_name

    def warmup(self) -> None:
        _ = self.generate_wav(instruction="neutral voice", text="warmup")

    def generate_wav(self, instruction: str, text: str) -> tuple[np.ndarray, int]:
        """Return (mono float32 waveform, sample_rate). Mock: 0.5 s silence at 24 kHz."""
        sample_rate = 24000
        num_samples = sample_rate // 2
        return np.zeros(num_samples, dtype=np.float32), sample_rate
