"""Tests for vocence.registry.wrapper_integrity helpers used by validate_miner."""
from vocence.registry.wrapper_integrity import (
    extract_approved_variables,
    is_valid_hf_revision,
)


SAMPLE_WRAPPER = '''
VOCENCE_REPO = "ratrys/sft-tts-800"
VOCENCE_REVISION = "067223f9d4dc95a010ed06c130970e48b19b1ecd"
VOCENCE_CHUTES_USER = "contabo"
VOCENCE_CHUTE_ID = "vocence-top-miner-2"
'''


def test_extract_approved_variables_double_quotes():
    out = extract_approved_variables(SAMPLE_WRAPPER)
    assert out["VOCENCE_REPO"] == "ratrys/sft-tts-800"
    assert out["VOCENCE_REVISION"] == "067223f9d4dc95a010ed06c130970e48b19b1ecd"
    assert out["VOCENCE_CHUTES_USER"] == "contabo"
    assert out["VOCENCE_CHUTE_ID"] == "vocence-top-miner-2"


def test_extract_approved_variables_single_quotes():
    src = "VOCENCE_REVISION = 'main'\nVOCENCE_REPO = 'a/b'\n"
    out = extract_approved_variables(src)
    assert out["VOCENCE_REVISION"] == "main"
    assert out["VOCENCE_REPO"] == "a/b"
    assert out["VOCENCE_CHUTE_ID"] == ""
    assert out["VOCENCE_CHUTES_USER"] == ""


def test_extract_approved_variables_ignores_comment_lookalikes():
    """A regex extractor would match a comment first; AST extractor must not."""
    src = (
        '# legit: VOCENCE_REVISION = "067223f9d4dc95a010ed06c130970e48b19b1ecd"\n'
        'VOCENCE_REVISION = "main"\n'
        'VOCENCE_REPO = "ratrys/sft-tts-800"\n'
    )
    out = extract_approved_variables(src)
    assert out["VOCENCE_REVISION"] == "main"
    assert out["VOCENCE_REPO"] == "ratrys/sft-tts-800"


def test_extract_approved_variables_ignores_nonconstant_assignments():
    """Only module-level Name = Constant(str) is recognized."""
    src = (
        '_x = "067223f9d4dc95a010ed06c130970e48b19b1ecd"\n'
        'VOCENCE_REVISION = _x\n'
    )
    out = extract_approved_variables(src)
    assert out["VOCENCE_REVISION"] == ""


def test_extract_approved_variables_syntax_error_returns_empty():
    out = extract_approved_variables("def broken( :\n")
    assert out == {
        "VOCENCE_REPO": "",
        "VOCENCE_REVISION": "",
        "VOCENCE_CHUTES_USER": "",
        "VOCENCE_CHUTE_ID": "",
    }


def test_extract_approved_variables_missing_returns_empty():
    out = extract_approved_variables("# empty file\n")
    assert out == {
        "VOCENCE_REPO": "",
        "VOCENCE_REVISION": "",
        "VOCENCE_CHUTES_USER": "",
        "VOCENCE_CHUTE_ID": "",
    }


def test_is_valid_hf_revision_accepts_sha():
    assert is_valid_hf_revision("067223f9d4dc95a010ed06c130970e48b19b1ecd") is True
    assert is_valid_hf_revision("0" * 40) is True


def test_is_valid_hf_revision_rejects_branches_and_tags():
    assert is_valid_hf_revision("main") is False
    assert is_valid_hf_revision("v1.0") is False
    assert is_valid_hf_revision("HEAD") is False


def test_is_valid_hf_revision_rejects_bad_format():
    # uppercase, short sha, with whitespace, empty
    assert is_valid_hf_revision("067223F9D4DC95A010ED06C130970E48B19B1ECD") is False
    assert is_valid_hf_revision("067223f9") is False
    assert is_valid_hf_revision(" 067223f9d4dc95a010ed06c130970e48b19b1ecd") is False
    assert is_valid_hf_revision("") is False
    assert is_valid_hf_revision(None) is False  # type: ignore[arg-type]
