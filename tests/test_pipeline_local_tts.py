"""Tests for the canonical-script hash gate and trait->instruction rendering."""

import hashlib
import pytest

from vocence.domain.spec import load_spec
from vocence.pipeline.local_tts import (
    verify_canonical_script, _instruction_from_traits, CanonicalScriptError,
)

SPEC = load_spec()


def test_verify_rejects_missing_script(tmp_path):
    with pytest.raises(CanonicalScriptError):
        verify_canonical_script(tmp_path, SPEC)


def test_verify_rejects_wrong_hash(tmp_path):
    (tmp_path / "miner.py").write_text("print('not the canonical script')")
    with pytest.raises(CanonicalScriptError):
        verify_canonical_script(tmp_path, SPEC)


def test_verify_accepts_matching_hash(tmp_path):
    import dataclasses
    content = b"# canonical\n"
    (tmp_path / "miner.py").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    spec = dataclasses.replace(SPEC, canonical_miner_py_sha256=digest)
    assert verify_canonical_script(tmp_path, spec) == digest


def test_instruction_from_traits():
    s = _instruction_from_traits({"gender": "female", "emotion": "calm", "pace": "slow"})
    assert "gender: female" in s and "emotion: calm" in s and "pace: slow" in s
    assert _instruction_from_traits({}) == ""
