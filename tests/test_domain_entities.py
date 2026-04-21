"""Tests for vocence.domain.entities."""

import pytest
from pydantic import ValidationError

from vocence.domain.entities import (
    ParticipantInfo,
    ChainCommitment,
    EvaluationOutcome,
    SourceAudioMetadata,
    GeneratedPrompt,
    AudioGenerationConfig,
)


class TestParticipantInfo:
    """Tests for ParticipantInfo model."""

    def test_minimal_valid(self):
        p = ParticipantInfo(uid=0, hotkey="0xabc")
        assert p.uid == 0
        assert p.hotkey == "0xabc"
        assert p.model_name == ""
        assert p.is_valid is False

    def test_full_valid(self):
        p = ParticipantInfo(
            uid=1,
            hotkey="0xdef",
            model_name="user/model",
            model_revision="rev",
            chute_id="c1",
            chute_slug="slug",
            block=100,
            is_valid=True,
        )
        assert p.chute_slug == "slug"
        assert p.is_valid is True


class TestChainCommitment:
    """Tests for ChainCommitment model."""

    def test_required_fields(self):
        c = ChainCommitment(
            hotkey="0x123",
            model_name="m",
            model_revision="r",
            chute_id="ch",
            commit_block=50,
        )
        assert c.commit_block == 50


class TestEvaluationOutcome:
    """Tests for EvaluationOutcome model."""

    def test_confidence_bounds(self):
        o = EvaluationOutcome(
            generated_wins=True,
            confidence=75,
            reasoning="test",
            presentation_order="first",
        )
        assert o.confidence == 75

    def test_confidence_ge_50(self):
        with pytest.raises(ValidationError):
            EvaluationOutcome(
                generated_wins=True,
                confidence=49,
                reasoning="x",
                presentation_order="x",
            )


class TestSourceAudioMetadata:
    """Tests for SourceAudioMetadata."""

    def test_valid(self):
        m = SourceAudioMetadata(
            bucket="b",
            key="k",
            full_duration_seconds=10.0,
            clip_start_seconds=1.0,
            clip_duration_seconds=5.0,
        )
        assert m.clip_duration_seconds == 5.0


class TestGeneratedPrompt:
    """Tests for GeneratedPrompt."""

    def test_default_model(self):
        p = GeneratedPrompt(text="Hello")
        assert p.model == "gpt-4o"
        assert p.text == "Hello"


class TestAudioGenerationConfig:
    """Tests for AudioGenerationConfig."""

    def test_defaults(self):
        c = AudioGenerationConfig()
        assert c.sample_rate == 22050
        assert c.format == "wav"
