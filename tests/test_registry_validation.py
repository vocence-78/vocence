"""Tests for vocence.registry.validation and commitment validation."""
import hashlib
from pathlib import Path

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

_CANONICAL_PATH = Path(__file__).resolve().parent.parent / "miner_sample" / "example_repo" / "miner.py"


def test_verify_miner_py_hash_match():
    """Canonical miner.py source should pass the hash check."""
    source = _CANONICAL_PATH.read_text(encoding="utf-8")
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


def test_verify_miner_py_hash_trivial_change():
    """Even a single appended newline should fail."""
    source = _CANONICAL_PATH.read_text(encoding="utf-8") + "\n"
    ok, reason = verify_miner_py_hash(source)
    assert ok is False
    assert reason == "miner_py_hash_mismatch"


def test_verify_miner_py_hash_comment_added():
    """Adding a comment to canonical source should fail."""
    source = _CANONICAL_PATH.read_text(encoding="utf-8") + "# sneaky comment\n"
    ok, reason = verify_miner_py_hash(source)
    assert ok is False
    assert reason == "miner_py_hash_mismatch"


def test_canonical_hash_matches_raw_bytes():
    """Owner-side text→encode path must produce same hash as raw bytes (runtime path)."""
    raw_hash = hashlib.sha256(_CANONICAL_PATH.read_bytes()).hexdigest()
    text_hash = hashlib.sha256(_CANONICAL_PATH.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    assert raw_hash == text_hash == CANONICAL_MINER_PY_SHA256


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


def test_verify_repo_manifest_extra_speech_tokenizer_file():
    """Extra file inside speech_tokenizer/ should be rejected."""
    files = _full_manifest() + ["speech_tokenizer/custom_vocab.json"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "extra_files" in reason
    assert "speech_tokenizer/custom_vocab.json" in reason


def test_verify_repo_manifest_pickle_weights():
    """Pickle-format weight files should be rejected as extra files."""
    for bad_file in ["model.bin", "model.pt", "pytorch_model.bin"]:
        files = _full_manifest() + [bad_file]
        ok, reason = verify_repo_manifest(files)
        assert ok is False, f"{bad_file} should be rejected"
        assert "extra_files" in reason


def test_verify_repo_manifest_speaker_embedding_file():
    """Speaker embedding files should be rejected."""
    files = _full_manifest() + ["speaker_embeddings.pt"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "extra_files" in reason


def test_verify_repo_manifest_missing_required():
    """Missing a required file should be rejected."""
    files = [f for f in _full_manifest() if f != "miner.py"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "missing_required_files" in reason
    assert "miner.py" in reason


def test_verify_repo_manifest_missing_speech_tokenizer():
    """Missing speech_tokenizer files should be rejected."""
    files = [f for f in _full_manifest() if not f.startswith("speech_tokenizer/")]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "missing_required_files" in reason


def test_verify_repo_manifest_missing_safetensors():
    """Missing model.safetensors should be rejected."""
    files = [f for f in _full_manifest() if f != "model.safetensors"]
    ok, reason = verify_repo_manifest(files)
    assert ok is False
    assert "missing_required_files" in reason
    assert "model.safetensors" in reason


def test_verify_repo_manifest_empty():
    """Empty file list should fail with all required files missing."""
    ok, reason = verify_repo_manifest([])
    assert ok is False
    assert "missing_required_files" in reason


def test_verify_repo_manifest_duplicates_still_pass():
    """Duplicate entries in file list should not cause false rejection."""
    files = _full_manifest() + ["miner.py"]
    ok, reason = verify_repo_manifest(files)
    assert ok is True


# --- Config constants sanity ---

def test_required_files_subset_of_manifest():
    """REPO_REQUIRED_FILES must be a subset of REPO_FILE_MANIFEST."""
    assert REPO_REQUIRED_FILES.issubset(REPO_FILE_MANIFEST)


def test_canonical_hash_is_valid_sha256():
    """CANONICAL_MINER_PY_SHA256 must be a 64-char hex string."""
    assert len(CANONICAL_MINER_PY_SHA256) == 64
    int(CANONICAL_MINER_PY_SHA256, 16)


def test_manifest_contains_miner_py():
    """miner.py must be in both manifest and required sets."""
    assert "miner.py" in REPO_FILE_MANIFEST
    assert "miner.py" in REPO_REQUIRED_FILES


def test_manifest_contains_speech_tokenizer():
    """speech_tokenizer/ files must be in both manifest and required sets."""
    st_files = {f for f in REPO_FILE_MANIFEST if f.startswith("speech_tokenizer/")}
    assert len(st_files) == 4
    assert st_files.issubset(REPO_REQUIRED_FILES)


def test_optional_files_not_in_required():
    """Optional files (.gitattributes, .gitignore, README.md) must not be required."""
    for f in [".gitattributes", ".gitignore", "README.md"]:
        assert f in REPO_FILE_MANIFEST
        assert f not in REPO_REQUIRED_FILES


def test_wrapper_template_hash_matches_config():
    """VOCENCE_CANONICAL_MINER_PY_HASH in wrapper template must match config constant."""
    template_path = Path(__file__).resolve().parent.parent / "miner_sample" / "chute_template" / "vocence_chute.py.jinja2"
    content = template_path.read_text(encoding="utf-8")
    assert CANONICAL_MINER_PY_SHA256 in content


def test_wrapper_template_manifest_matches_config():
    """File manifest in wrapper template must contain all the same entries as config."""
    template_path = Path(__file__).resolve().parent.parent / "miner_sample" / "chute_template" / "vocence_chute.py.jinja2"
    content = template_path.read_text(encoding="utf-8")
    for f in REPO_FILE_MANIFEST:
        assert f'"{f}"' in content, f"manifest entry {f!r} missing from wrapper template"


def test_wrapper_templates_in_sync():
    """Both wrapper template copies must be identical."""
    chute_path = Path(__file__).resolve().parent.parent / "miner_sample" / "chute_template" / "vocence_chute.py.jinja2"
    canonical_path = Path(__file__).resolve().parent.parent / "vocence" / "registry" / "canonical_wrapper_template.jinja2"
    assert chute_path.read_bytes() == canonical_path.read_bytes()
