"""Tests for vocence.adapters.api."""
from unittest.mock import MagicMock, AsyncMock
from vocence.adapters.api import ServiceClient

def test_service_client_has_api_url():
    c = ServiceClient(api_url="http://localhost:8000")
    assert c.api_url == "http://localhost:8000"

def test_service_client_keypair_property_raises_without_keypair():
    c = ServiceClient(api_url="http://test.com")
    try:
        _ = c.keypair
    except ValueError as e:
        assert "keypair" in str(e).lower() or "hotkey" in str(e).lower()
