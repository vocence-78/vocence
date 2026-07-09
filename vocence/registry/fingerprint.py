"""Model fingerprinting for near-duplicate detection (gauntlet check #4).

Miners must submit genuinely different models; a fine-tune that is a near-copy of an
existing king is rejected. We build a deterministic, CPU-only fingerprint from each
tensor's summary statistics (so honest validators compute identical fingerprints) and
compare with cosine similarity. It is a heuristic — cheap and reproducible — meant to
catch trivial clones, not to prove novelty.

The pure math (signatures, similarity, store) is numpy-only and unit-tested. Reading
weights from ``.safetensors`` lazily imports the ``safetensors`` library (validator
GPU/CPU host only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


def tensor_signature(arr: "np.ndarray") -> List[float]:
    """A small, order-invariant statistical signature of one tensor."""
    a = np.asarray(arr, dtype=np.float64).ravel()
    if a.size == 0:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    return [
        float(a.mean()),
        float(a.std()),
        float(np.linalg.norm(a) / (a.size ** 0.5)),  # rms
        float(a.min()),
        float(a.max()),
    ]


def fingerprint_from_tensors(named: Dict[str, "np.ndarray"]) -> List[float]:
    """Concatenate per-tensor signatures in canonical (name-sorted) order."""
    vec: List[float] = []
    for name in sorted(named):
        vec.extend(tensor_signature(named[name]))
    return vec


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two fingerprints (0 if either is empty/zero or lengths differ)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.clip(np.dot(va, vb) / (na * nb), -1.0, 1.0))


@dataclass
class FingerprintStore:
    """Fingerprints of previously-accepted models, for max-similarity queries."""

    entries: List[Tuple[str, List[float]]] = field(default_factory=list)

    def add(self, ref: str, vec: List[float]) -> None:
        self.entries.append((ref, vec))

    def max_similarity(self, vec: List[float], *, exclude_ref: Optional[str] = None) -> Optional[float]:
        sims = [
            cosine_similarity(vec, other)
            for ref, other in self.entries
            if ref != exclude_ref
        ]
        return max(sims) if sims else None


def fingerprint_safetensors(model_dir: str | Path) -> List[float]:  # pragma: no cover - needs safetensors + weights
    """Fingerprint every ``*.safetensors`` shard in a model directory."""
    try:
        from safetensors import safe_open
    except ImportError as exc:
        raise RuntimeError("fingerprinting needs the 'safetensors' package installed") from exc

    named: Dict[str, "np.ndarray"] = {}
    for shard in sorted(Path(model_dir).glob("**/*.safetensors")):
        with safe_open(str(shard), framework="np") as f:  # type: ignore
            for key in f.keys():
                named[f"{shard.name}:{key}"] = f.get_tensor(key)
    return fingerprint_from_tensors(named)
