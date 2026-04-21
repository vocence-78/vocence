"""Tests for vocence.domain.config."""
from vocence.domain import config

def test_subnet_id_integer():
    assert isinstance(config.SUBNET_ID, int)

def test_audio_buckets_set():
    assert config.AUDIO_SOURCE_BUCKET and config.AUDIO_SAMPLES_BUCKET
