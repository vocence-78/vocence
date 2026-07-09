"""Assemble gauntlet inputs from a model directory and run the full validation.

Shared by the miner CLI (`vocence miner check`) and the validator (after it downloads
a challenger from Hippius). Reads the file list, the canonical-script hash, and
``config.json`` from disk, computes the near-duplicate similarity (CPU fingerprint),
and runs the deterministic gauntlet. The fingerprint step is injectable so the
assembly is testable without the ``safetensors`` package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, Optional

from vocence.domain.spec import SubnetSpec
from vocence.registry.gauntlet import run_gauntlet, GauntletResult
from vocence.registry.fingerprint import FingerprintStore, fingerprint_safetensors
from vocence.adapters.model_store import build_manifest, file_sha256, compute_dir_digest

SimilarityFn = Callable[[str], Optional[float]]


def _default_similarity(store: Optional[FingerprintStore]) -> SimilarityFn:
    def _fn(model_dir: str) -> Optional[float]:  # pragma: no cover - needs safetensors + weights
        if store is None:
            return None
        vec = fingerprint_safetensors(model_dir)
        sim = store.max_similarity(vec)
        store.add(model_dir, vec)  # remember for future dedup within this run
        return sim
    return _fn


def assemble_and_validate(
    model_dir: str | Path,
    repo: str,
    spec: SubnetSpec,
    *,
    seed_cfg: Dict[str, object],
    store: Optional[FingerprintStore] = None,
    similarity_fn: Optional[SimilarityFn] = None,
) -> GauntletResult:
    model_dir = str(model_dir)
    files = list(build_manifest(model_dir).keys())
    miner_sha = file_sha256(model_dir, spec.forbidden_py_except or "miner.py")
    digest = compute_dir_digest(model_dir)

    config_path = Path(model_dir) / "config.json"
    candidate_cfg = json.loads(config_path.read_text()) if config_path.is_file() else {}

    sim_fn = similarity_fn or _default_similarity(store)
    max_similarity = sim_fn(model_dir)

    return run_gauntlet(
        repo=repo, digest=digest, files=files, candidate_cfg=candidate_cfg,
        seed_cfg=seed_cfg, spec=spec, miner_py_sha256=miner_sha, max_similarity=max_similarity,
    )
