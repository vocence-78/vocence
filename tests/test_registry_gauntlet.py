"""Tests for the deterministic validation gauntlet."""

import pytest

from vocence.domain.spec import load_spec
from vocence.registry.gauntlet import (
    check_repo_pattern,
    check_file_manifest,
    check_architecture,
    check_duplicate,
    run_gauntlet,
)

SPEC = load_spec()
CANONICAL = SPEC.canonical_miner_py_sha256


def _valid_files():
    return list(SPEC.required_files) + [".gitattributes", "README.md"]


def _seed_cfg():
    return {
        "architectures": ["Qwen3ForCausalLM"],
        **{k: i for i, k in enumerate(SPEC.arch_lock_keys)},
    }


def test_repo_pattern():
    assert check_repo_pattern("ns/vocence-prompttts-v1", SPEC).ok
    assert not check_repo_pattern("ns/something-else", SPEC).ok


def test_file_manifest_happy():
    out = check_file_manifest(_valid_files(), SPEC, miner_py_sha256=CANONICAL)
    assert out.ok, out.reason


def test_file_manifest_missing_required():
    files = [f for f in _valid_files() if f != "config.json"]
    out = check_file_manifest(files, SPEC, miner_py_sha256=CANONICAL)
    assert not out.ok and "missing" in out.reason


def test_file_manifest_rejects_extra():
    out = check_file_manifest(_valid_files() + ["sneaky.bin"], SPEC, miner_py_sha256=CANONICAL)
    assert not out.ok and "extra" in out.reason


def test_file_manifest_rejects_rogue_py():
    out = check_file_manifest(_valid_files() + ["evil.py"], SPEC, miner_py_sha256=CANONICAL)
    assert not out.ok and "python" in out.reason


def test_file_manifest_rejects_wrong_miner_hash():
    out = check_file_manifest(_valid_files(), SPEC, miner_py_sha256="deadbeef")
    assert not out.ok and "mismatch" in out.reason


def test_architecture_happy():
    assert check_architecture(_seed_cfg(), _seed_cfg(), SPEC).ok


def test_architecture_rejects_auto_map():
    cfg = _seed_cfg(); cfg["auto_map"] = {"x": "y"}
    assert not check_architecture(cfg, _seed_cfg(), SPEC).ok


def test_architecture_rejects_quant():
    cfg = _seed_cfg(); cfg["quantization_config"] = {"bits": 4}
    assert not check_architecture(cfg, _seed_cfg(), SPEC).ok


def test_architecture_rejects_lock_key_mismatch():
    cfg = _seed_cfg()
    key = SPEC.arch_lock_keys[0]
    cfg[key] = 99999
    out = check_architecture(cfg, _seed_cfg(), SPEC)
    assert not out.ok and key in out.reason


def test_duplicate():
    assert check_duplicate(None, SPEC).ok
    assert check_duplicate(0.90, SPEC).ok
    assert not check_duplicate(0.96, SPEC).ok


def test_run_gauntlet_full_pass():
    res = run_gauntlet(
        repo="ns/vocence-prompttts-v1", digest="sha256:" + "a" * 64,
        files=_valid_files(), candidate_cfg=_seed_cfg(), seed_cfg=_seed_cfg(),
        spec=SPEC, miner_py_sha256=CANONICAL, max_similarity=0.1,
    )
    assert res.valid
    assert [c.name for c in res.checks] == ["repo_pattern", "file_manifest", "architecture", "duplicate"]


def test_run_gauntlet_short_circuits():
    res = run_gauntlet(
        repo="bad/name", digest="sha256:" + "a" * 64,
        files=_valid_files(), candidate_cfg=_seed_cfg(), seed_cfg=_seed_cfg(),
        spec=SPEC, miner_py_sha256=CANONICAL,
    )
    assert not res.valid
    assert len(res.checks) == 1  # stopped at repo_pattern
    assert res.first_failure.name == "repo_pattern"
