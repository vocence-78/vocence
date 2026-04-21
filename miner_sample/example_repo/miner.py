"""
Example Vocence PromptTTS engine (MOCK). Replace with your real model.

Contract: this file must be named miner.py and define class Miner with
  - __init__(path_hf_repo: Path)
  - warmup() -> None
  - generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


class Miner:
    """Mock PromptTTS engine: returns silence. Replace with your real model loading and synthesis."""

    def __init__(self, path_hf_repo: Path) -> None:
        self._repo_path = Path(path_hf_repo).resolve()
        self._config = {}
        # In a real miner: load config from path_hf_repo / "vocence_config.yaml", load model, etc.

    def warmup(self) -> None:
        _ = self.generate_wav(instruction="neutral voice", text="warmup")

    def generate_wav(self, instruction: str, text: str) -> tuple[np.ndarray, int]:
        """Return (mono float32 waveform, sample_rate). Mock: 0.5 s silence at 24 kHz."""
        sample_rate = 24000
        num_samples = sample_rate // 2
        return np.zeros(num_samples, dtype=np.float32), sample_rate
