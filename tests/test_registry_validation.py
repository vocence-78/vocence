"""Tests for vocence.registry.validation and commitment validation."""
from vocence.adapters.chain import validate_commitment_fields
from vocence.domain.entities import ParticipantInfo
from vocence.registry.validation import CHUTE_NAME_MAGIC_WORD


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
