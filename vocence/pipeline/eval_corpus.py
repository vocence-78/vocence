"""Fixed, pinned evaluation corpus.

Every validator evaluates the identical (target_text, trait_instruction) prompts so
the duel is reproducible. The corpus is published (e.g. on Hippius) as a JSON manifest
and pinned by hash in ``vocence.toml`` — loading verifies the hash so a tampered or
drifted corpus is rejected before it can skew a coronation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List

from vocence.pipeline.duel import CorpusSample


def corpus_hash(raw: bytes) -> str:
    """``sha256:<hex>`` of the raw manifest bytes (what the spec pins)."""
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_corpus(raw: bytes, *, expected_hash: str | None = None) -> List[CorpusSample]:
    """Parse a corpus manifest (JSON array of samples), optionally verifying its hash.

    Each entry: ``{"sample_id", "target_text", "traits": {...}}``.
    Raises ValueError on hash mismatch or malformed entries.
    """
    if expected_hash:
        actual = corpus_hash(raw)
        if actual != expected_hash.strip().lower():
            raise ValueError(f"corpus hash mismatch: pinned {expected_hash}, got {actual}")

    data = json.loads(raw)
    items = data["samples"] if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("corpus manifest must be a list (or {'samples': [...]})")

    samples: List[CorpusSample] = []
    seen: set[str] = set()
    for i, entry in enumerate(items):
        sid = str(entry.get("sample_id") or f"sample-{i:05d}")
        if sid in seen:
            raise ValueError(f"duplicate sample_id: {sid}")
        seen.add(sid)
        text = str(entry.get("target_text", "")).strip()
        if not text:
            raise ValueError(f"sample {sid} has empty target_text")
        traits: Dict[str, object] = dict(entry.get("traits", {}) or {})
        samples.append(CorpusSample(sample_id=sid, target_text=text, traits=traits))
    return samples


def load_corpus_file(path: str | Path, *, expected_hash: str | None = None) -> List[CorpusSample]:
    return parse_corpus(Path(path).read_bytes(), expected_hash=expected_hash)
