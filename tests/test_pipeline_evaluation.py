"""Tests for vocence.pipeline.evaluation."""
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from vocence.pipeline.evaluation import (
    ELEMENT_WEIGHTS,
    VOICE_TRAIT_ENUMS,
    forced_choice_assessment_async,
    format_task_prompt_for_tts,
    generate_description_async,
    get_transcription_and_traits_async,
    score_element,
    score_miner_against_spec_async,
    score_traits_against_spec,
    word_error_rate,
)


@pytest.fixture
def temp_wav_path():
    """Minimal valid WAV file (44-byte header + a few samples)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x22\x56\x00\x00\x44\xac\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
        return f.name


SOURCE_TRAITS_JSON = (
    '{"transcription": "hello world", "gender": "female", '
    '"pitch": "mid", "speed": "normal", "age_group": "adult", '
    '"emotion": "happy", "tone": "warm", "accent": "uk"}'
)

MINER_TRAITS_JSON_PERFECT = SOURCE_TRAITS_JSON
MINER_TRAITS_JSON_OFF = (
    '{"transcription": "hello world", "gender": "female", '
    '"pitch": "high", "speed": "normal", "age_group": "adult", '
    '"emotion": "neutral", "tone": "formal", "accent": "us"}'
)


def _make_judge_mock(response_json: str) -> MagicMock:
    judge = MagicMock()
    judge.judge_audio_pointwise.return_value = {"success": True, "response": response_json}
    return judge


@pytest.mark.asyncio
async def test_generate_description_returns_string(mock_openai_client, temp_wav_path):
    with patch("vocence.pipeline.evaluation._get_judge", return_value=_make_judge_mock(SOURCE_TRAITS_JSON)):
        try:
            result = await generate_description_async(mock_openai_client, temp_wav_path)
            assert isinstance(result, str)
            assert "hello world" in result
            assert "gender: female" in result
            assert "age_group: adult" in result
        finally:
            import os
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)


@pytest.mark.asyncio
async def test_get_transcription_and_traits_returns_normalized_dict(mock_openai_client, temp_wav_path):
    with patch("vocence.pipeline.evaluation._get_judge", return_value=_make_judge_mock(SOURCE_TRAITS_JSON)):
        try:
            result = await get_transcription_and_traits_async(mock_openai_client, temp_wav_path)
            assert result["transcription"] == "hello world"
            assert result["gender"] == "female"
            assert result["pitch"] == "mid"
            assert result["accent"] == "uk"
            # every categorical key is populated with an enum value
            for key, enum in VOICE_TRAIT_ENUMS.items():
                assert result[key] in enum
        finally:
            import os
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)


@pytest.mark.asyncio
async def test_get_transcription_and_traits_coerces_aliases(mock_openai_client, temp_wav_path):
    legacy_json = (
        '{"transcription": "hi", "gender": "unknown", "pitch": "normal", '
        '"speed": "normal", "age_group": "twenties", "emotion": "bored", '
        '"tone": "neutral", "accent": "american"}'
    )
    with patch("vocence.pipeline.evaluation._get_judge", return_value=_make_judge_mock(legacy_json)):
        try:
            result = await get_transcription_and_traits_async(mock_openai_client, temp_wav_path)
            assert result["gender"] == "neutral"
            assert result["pitch"] == "mid"
            assert result["age_group"] == "young_adult"
            assert result["emotion"] == "neutral"
            assert result["tone"] == "casual"
            assert result["accent"] == "us"
        finally:
            import os
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)


def test_format_task_prompt_for_tts():
    traits = {
        "transcription": "hi there",
        "gender": "male",
        "pitch": "mid",
        "speed": "normal",
        "age_group": "adult",
        "emotion": "neutral",
        "tone": "casual",
        "accent": "us",
    }
    out = format_task_prompt_for_tts(traits)
    assert out.startswith("hi there")
    assert "gender: male" in out
    assert "age_group: adult" in out
    assert "environment" not in out


def test_element_weights_sum_to_one():
    assert abs(sum(ELEMENT_WEIGHTS.values()) - 1.0) < 1e-9


def test_word_error_rate_perfect_and_miss():
    assert word_error_rate("hello world", "hello world") == 0.0
    # one word wrong out of two → WER = 0.5
    assert word_error_rate("hello world", "hello there") == 0.5
    # empty ref, non-empty hyp → 1.0 (nothing to match)
    assert word_error_rate("", "anything") == 1.0
    # empty ref, empty hyp → 0.0
    assert word_error_rate("", "") == 0.0


def test_score_element_exact_and_ordinal():
    # script uses WER
    assert score_element("script", "hello world", "hello world") == 1.0
    assert 0.0 < score_element("script", "hello world", "hello there") < 1.0
    # ordinal: pitch low vs mid = off by one = 0.5
    assert score_element("pitch", "low", "mid") == 0.5
    assert score_element("pitch", "low", "high") == 0.0
    assert score_element("pitch", "mid", "mid") == 1.0
    # categorical exact: gender
    assert score_element("gender", "male", "male") == 1.0
    assert score_element("gender", "male", "female") == 0.0


def test_score_traits_against_spec_perfect_is_one():
    spec = {"transcription": "hello", "gender": "male", "pitch": "mid",
            "speed": "normal", "age_group": "adult", "emotion": "neutral",
            "tone": "casual", "accent": "us"}
    score, breakdown = score_traits_against_spec(spec, spec)
    assert score == 1.0
    for elt, row in breakdown.items():
        assert row["score"] == 1.0
        assert row["weight"] == ELEMENT_WEIGHTS[elt]


def test_score_traits_against_spec_partial():
    spec = {"transcription": "hello world", "gender": "female", "pitch": "mid",
            "speed": "normal", "age_group": "adult", "emotion": "happy",
            "tone": "warm", "accent": "uk"}
    miner = {"transcription": "hello world", "gender": "female", "pitch": "high",
             "speed": "normal", "age_group": "adult", "emotion": "neutral",
             "tone": "formal", "accent": "us"}
    score, breakdown = score_traits_against_spec(spec, miner)
    assert 0.0 < score < 1.0
    assert breakdown["script"]["score"] == 1.0
    assert breakdown["pitch"]["score"] == 0.5  # off by one ordinal
    assert breakdown["emotion"]["score"] == 0.0
    assert breakdown["tone"]["score"] == 0.0
    assert breakdown["accent"]["score"] == 0.0


@pytest.mark.asyncio
async def test_score_miner_against_spec_async_without_source_audio(mock_openai_client, temp_wav_path):
    """When source_audio_path is not passed, naturalness is skipped and weights renormalize."""
    source_traits = {
        "transcription": "hello world", "gender": "female", "pitch": "mid",
        "speed": "normal", "age_group": "adult", "emotion": "happy",
        "tone": "warm", "accent": "uk",
    }
    with patch("vocence.pipeline.evaluation._get_judge", return_value=_make_judge_mock(MINER_TRAITS_JSON_OFF)):
        try:
            result = await score_miner_against_spec_async(mock_openai_client, temp_wav_path, source_traits)
            assert 0.0 <= result["score"] <= 1.0
            assert isinstance(result["generated_wins"], bool)
            assert result["confidence"] == int(round(result["score"] * 100))
            # naturalness is optional — breakdown covers the other 8 elements.
            assert "naturalness" not in result["breakdown"]
            non_naturalness_elements = set(ELEMENT_WEIGHTS) - {"naturalness"}
            assert set(result["breakdown"].keys()) == non_naturalness_elements
            assert result["naturalness"] is None
            assert result["extracted_traits"]["gender"] == "female"
        finally:
            import os
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)


@pytest.mark.asyncio
async def test_score_miner_against_spec_async_with_naturalness(mock_openai_client, temp_wav_path):
    """With source_audio_path, naturalness is computed and rolls into the weighted score."""
    source_traits = {
        "transcription": "hello world", "gender": "female", "pitch": "mid",
        "speed": "normal", "age_group": "adult", "emotion": "happy",
        "tone": "warm", "accent": "uk",
    }
    judge = MagicMock()
    judge.judge_audio_pointwise.return_value = {"success": True, "response": MINER_TRAITS_JSON_PERFECT}
    judge.judge_audio.return_value = {"success": True, "response": "FIRST\nmore natural prosody"}
    path2 = temp_wav_path + ".src.wav"
    import shutil, os
    shutil.copy(temp_wav_path, path2)
    with patch("vocence.pipeline.evaluation._get_judge", return_value=judge), \
         patch("vocence.pipeline.evaluation.random.choice", return_value=False):  # no swap → miner is second
        try:
            result = await score_miner_against_spec_async(
                mock_openai_client, temp_wav_path, source_traits,
                source_audio_path=path2, task_description="hello world | gender: female",
            )
            assert "naturalness" in result["breakdown"]
            # no swap + FIRST → source wins naturalness, miner loses it (score 0 on that element)
            assert result["breakdown"]["naturalness"]["score"] == 0.0
            assert result["naturalness"]["miner_more_natural"] is False
            # All other elements match perfectly → overall = 1.0 - ELEMENT_WEIGHTS["naturalness"]
            expected_score = 1.0 - ELEMENT_WEIGHTS["naturalness"]
            assert abs(result["score"] - expected_score) < 1e-4
        finally:
            if os.path.exists(path2):
                os.remove(path2)
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)


@pytest.mark.asyncio
async def test_forced_choice_returns_dict_back_compat(mock_openai_client, temp_wav_path):
    """forced_choice_assessment_async is kept as a back-compat wrapper over the spec scorer."""
    path2 = temp_wav_path + ".2.wav"
    import shutil
    shutil.copy(temp_wav_path, path2)
    judge = MagicMock()
    judge.judge_audio_pointwise.return_value = {"success": True, "response": MINER_TRAITS_JSON_PERFECT}
    # Mock pairwise so miner wins naturalness deterministically.
    judge.judge_audio.return_value = {"success": True, "response": "SECOND\nclearer prosody"}
    with patch("vocence.pipeline.evaluation._get_judge", return_value=judge), \
         patch("vocence.pipeline.evaluation.random.choice", return_value=False):  # no swap → miner is second → miner wins
        try:
            result = await forced_choice_assessment_async(
                mock_openai_client, temp_wav_path, path2, "prompt"
            )
            assert "original_won" in result
            assert "generated_won" in result
            assert "score" in result
            assert "breakdown" in result
            assert result["generated_won"] is True
            assert result["score"] == 1.0
            assert result["naturalness"]["miner_more_natural"] is True
        finally:
            import os
            if os.path.exists(path2):
                os.remove(path2)
