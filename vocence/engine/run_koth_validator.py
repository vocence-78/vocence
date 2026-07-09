"""KOTH validator run-loop assembly (additive; does not touch the legacy `serve`).

Wires the concrete pieces into the coordinator and runs one cycle per weight-set
window. GPU is engaged only lazily, when the judges/generators load their models at
runtime — the assembly and scheduling logic here are pure/CPU and unit-tested via
:func:`should_run_cycle`. A validator runs this on a GPU host once the models are
served; until then the legacy path is untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from vocence.domain.spec import SubnetSpec
from vocence.pipeline.duel import CorpusSample
from vocence.pipeline.cache import GenerationCache, cached_generator
from vocence.pipeline.local_tts import load_generator
from vocence.pipeline.judges import (
    WhisperIntelligibilityJudge, AdherenceChecklistJudge, SpeechJudgeNaturalness,
)
from vocence.engine.koth_coordinator import Judges, run_cycle
from vocence.registry.fingerprint import FingerprintStore
from vocence.registry.local_check import assemble_and_validate


def should_run_cycle(block: int, cycle_length: int, offset: int, tolerance: int = 2) -> bool:
    """True when ``block`` is within ``tolerance`` of a cycle boundary (block % L == offset).

    Block-aligned so all honest validators act on the same window.
    """
    if cycle_length <= 0:
        return False
    phase = block % cycle_length
    target = offset % cycle_length
    diff = min((phase - target) % cycle_length, (target - phase) % cycle_length)
    return diff <= tolerance


def build_judges(spec: SubnetSpec, *, votes: int = 1) -> Judges:  # pragma: no cover - GPU path
    """Construct the three judges; models load lazily on first use."""
    return Judges(
        intelligibility=WhisperIntelligibilityJudge(spec),
        adherence=AdherenceChecklistJudge(spec),
        naturalness=SpeechJudgeNaturalness(spec, votes=votes),
    )


def make_generator_factory(spec: SubnetSpec, fetch_model, cache: GenerationCache):  # pragma: no cover - GPU/IO
    """Return an async (repo, digest) -> GenerateFn that downloads, loads, and caches.

    ``fetch_model(repo, digest) -> local_dir`` is the Hippius download (injected).
    """
    async def factory(repo: str, digest: str):
        local_dir = await fetch_model(repo, digest)
        base = load_generator(local_dir, spec)  # verifies canonical-script hash
        return cached_generator(base, digest, cache)
    return factory


def make_validator(spec: SubnetSpec, seed_cfg, fetch_model, store: FingerprintStore):  # pragma: no cover - IO
    """Return an async validate(candidate) -> (ok, reason) that downloads + runs the gauntlet."""
    async def validate(candidate):
        local_dir = await fetch_model(candidate.repo, candidate.digest)
        result = assemble_and_validate(local_dir, candidate.repo, spec, seed_cfg=seed_cfg, store=store)
        return result.valid, ("" if result.valid else (result.first_failure.reason if result.first_failure else "invalid"))
    return validate
