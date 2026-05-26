"""Tests for vocence.registry.validation and commitment validation."""
from vocence.adapters.chain import validate_commitment_fields
from vocence.domain.config import (
    CANONICAL_MINER_PY_SHA256,
    REPO_FILE_MANIFEST,
    REPO_REQUIRED_FILES,
)
from vocence.domain.entities import ParticipantInfo
from vocence.registry.source_audit import verify_miner_py_hash
from vocence.registry.validation import CHUTE_NAME_MAGIC_WORD, verify_repo_manifest


def test_chute_name_magic_word():
    """Chute name must contain magic word (case-insensitive) for owner validation."""
    assert CHUTE_NAME_MAGIC_WORD == "vocence"
    assert CHUTE_NAME_MAGIC_WORD in "vocence-parler-tts-010"
    assert CHUTE_NAME_MAGIC_WORD in "VOCENCE-prompttts".lower()
    assert CHUTE_NAME_MAGIC_WORD not in "parler-tts-010".lower()
    assert CHUTE_NAME_MAGIC_WORD not in "".lower()


def test_valid_commitment_passes(sample_commitment_dict):
    valid, err = validate_commitment_fields(sample_commitment_dict)
    assert valid is True
    assert err is None

def test_participant_info_from_commitment():
    p = ParticipantInfo(uid=0, hotkey="0xabc", model_name="m", chute_id="ch1", is_valid=True)
    assert p.chute_id == "ch1"


# --- miner.py canonical hash check ---

def test_verify_miner_py_hash_match():
    """Canonical miner.py source should pass the hash check."""
    from pathlib import Path
    canonical_path = Path(__file__).resolve().parent.parent / "miner_sample" / "example_repo" / "miner.py"
    source = canonical_path.read_text(encoding="utf-8")
    ok, reason = verify_miner_py_hash(source)
    assert ok is True
    assert reason is None


def test_verify_miner_py_hash_mismatch():
    """Modified miner.py should fail the hash check."""
    ok, reason = verify_miner_py_hash("# not the canonical miner.py\nprint('hello')\n")
    assert ok is False
    assert reason == "miner_py_hash_mismatch"


def test_verify_miner_py_hash_empty():
    """Empty source should fail."""
    ok, reason = verify_miner_py_hash("")
    assert ok is False
    assert reason == "miner_py_empty"


# --- File manifest validation ---

def _full_manifest():
    return list(REPO_FILE_MANIFEST)


def test_verify_repo_manifest_valid():
    """Complete manifest passes."""
    ok, reason = verify_repo_manifest(_full_manifest())
    assert ok is True
    assert reason is None


def test_verify_repo_manifest_valid_without_optional():
    """Manifest without optional files (.gitattributes, .gitignore, README.md) passes."""
    files = [f for f in _full_manifest() if f not in {".gitattributes", ".gitignore", "README.md"}]
    ok, reason = verify_repo_manifest(files)
    assert ok is True


def test_verify_repo_manifest_extra_files():
    """Extra files should be rejected."""
    files = _full_manifest() + ["sneaky_script.py"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert reason is not None
    assert "extra_files" in reason
    assert "sneaky_script.py" in reason


def test_verify_repo_manifest_hidden_files():
    """Hidden dotfiles not in the whitelist should be rejected."""
    files = _full_manifest() + [".secret_config"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "extra_files" in reason


def test_verify_repo_manifest_extra_directory():
    """Files in unexpected subdirectories should be rejected."""
    files = _full_manifest() + ["custom_code/payload.py"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "extra_files" in reason


def test_verify_repo_manifest_missing_required():
    """Missing a required file should be rejected."""
    files = [f for f in _full_manifest() if f != "miner.py"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert reason is not None
    assert "missing_required_files" in reason
    assert "miner.py" in reason


def test_verify_repo_manifest_missing_speech_tokenizer():
    """Missing speech_tokenizer files should be rejected."""
    files = [f for f in _full_manifest() if not f.startswith("speech_tokenizer/")]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "missing_required_files" in reason


# --- Config constants sanity ---

def test_required_files_subset_of_manifest():
    """REPO_REQUIRED_FILES must be a subset of REPO_FILE_MANIFEST."""
    assert REPO_REQUIRED_FILES.issubset(REPO_FILE_MANIFEST)


def test_canonical_hash_is_valid_sha256():
    """CANONICAL_MINER_PY_SHA256 must be a 64-char hex string."""
    assert len(CANONICAL_MINER_PY_SHA256) == 64
    int(CANONICAL_MINER_PY_SHA256, 16)  # should not raise
