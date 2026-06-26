"""Tests for vocence.domain.config."""
from vocence.domain import config

def test_subnet_id_integer():
    assert isinstance(config.SUBNET_ID, int)

def test_audio_config_set():
    assert config.AUDIO_SAMPLES_BUCKET and config.CORPUS_LOCAL_DIR
