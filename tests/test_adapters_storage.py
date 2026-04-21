"""Tests for vocence.adapters.storage."""
from unittest.mock import patch
from vocence.adapters.storage import create_storage_client

def test_returns_minio_client():
    with patch("vocence.adapters.storage.Minio") as m:
        client = create_storage_client()
        assert m.called and client is m.return_value
