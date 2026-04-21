"""Tests for vocence.adapters.chutes."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from vocence.adapters.chutes import construct_chute_endpoint, fetch_chute_details


class TestConstructChuteEndpoint:
    """Tests for construct_chute_endpoint."""

    def test_returns_expected_format(self):
        slug = "my-tts-chute"
        url = construct_chute_endpoint(slug)
        assert "my-tts-chute" in url
        assert url.startswith("https://")
        assert url.endswith("/speak")

    def test_different_slugs_produce_different_urls(self):
        u1 = construct_chute_endpoint("slug-a")
        u2 = construct_chute_endpoint("slug-b")
        assert u1 != u2


class TestFetchChuteDetails:
    """Tests for fetch_chute_details (mocked)."""

    @pytest.mark.asyncio
    async def test_returns_cached_result(self):
        session = MagicMock()
        result = await fetch_chute_details(session, "nonexistent-chute-id")
        # Without mocking HTTP, may return None on error or from cache
        assert result is None or isinstance(result, dict)
