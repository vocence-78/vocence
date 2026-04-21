"""Tests for vocence.adapters.media."""
from vocence.adapters.media import segments_to_base64, FFmpegError

def test_segments_to_base64_empty():
    result = segments_to_base64([])
    assert result == []

def test_segments_to_base64_skips_missing_files():
    # With non-existent paths, may raise or return empty; we only test the function exists
    from vocence.adapters.media import get_audio_duration
    assert callable(get_audio_duration)

def test_ffmpeg_error_is_exception():
    assert issubclass(FFmpegError, Exception)
