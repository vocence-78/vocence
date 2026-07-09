"""Tests for cycle scheduling and the assemble-and-validate integration."""

import dataclasses
import hashlib
import json

from vocence.domain.spec import load_spec
from vocence.engine.run_koth_validator import should_run_cycle
from vocence.registry.local_check import assemble_and_validate

SPEC = load_spec()


def test_should_run_cycle_window():
    assert should_run_cycle(150, 150, 0, tolerance=2) is True     # exact boundary
    assert should_run_cycle(151, 150, 0, tolerance=2) is True     # within tolerance
    assert should_run_cycle(140, 150, 0, tolerance=2) is False    # too far
    assert should_run_cycle(2, 150, 0, tolerance=2) is True       # wraps around 0
    assert should_run_cycle(100, 0, 0) is False                   # guard


def _seed_cfg():
    return {"architectures": ["Qwen3ForCausalLM"], **{k: i for i, k in enumerate(SPEC.arch_lock_keys)}}


def _build_valid_model(tmp_path):
    miner_bytes = b"# canonical inference script\n"
    for rel in SPEC.required_files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel == "config.json":
            p.write_text(json.dumps(_seed_cfg()))
        elif rel == "miner.py":
            p.write_bytes(miner_bytes)
        else:
            p.write_bytes(b"x")
    return hashlib.sha256(miner_bytes).hexdigest()


def test_assemble_and_validate_pass(tmp_path):
    miner_hash = _build_valid_model(tmp_path)
    spec = dataclasses.replace(SPEC, canonical_miner_py_sha256=miner_hash)
    result = assemble_and_validate(
        tmp_path, "ns/vocence-prompttts-v1", spec,
        seed_cfg=_seed_cfg(), similarity_fn=lambda d: 0.1,  # inject: not a duplicate
    )
    assert result.valid, result.first_failure


def test_assemble_and_validate_flags_duplicate(tmp_path):
    miner_hash = _build_valid_model(tmp_path)
    spec = dataclasses.replace(SPEC, canonical_miner_py_sha256=miner_hash)
    result = assemble_and_validate(
        tmp_path, "ns/vocence-prompttts-v1", spec,
        seed_cfg=_seed_cfg(), similarity_fn=lambda d: 0.99,  # near-duplicate
    )
    assert not result.valid
    assert result.first_failure.name == "duplicate"
