"""
Pytest fixtures for Vocence tests.

Provides mocks for Minio, OpenAI, Bittensor, and environment variables.
Loads .env from project root so tests see HIPPIUS_ACCESS_KEY, etc.
"""

import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

# Load .env from project root so env vars (e.g. HIPPIUS_*) are available in tests
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key:
                    # Remove surrounding quotes if present
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                        value = value[1:-1]
                    os.environ.setdefault(key, value)


@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    """Reset env to a known state for config tests (optional)."""
    yield
    # Cleanup if needed
    pass


@pytest.fixture
def mock_minio_client():
    """Minio client that returns empty list for list_objects."""
    client = MagicMock()
    client.list_objects = MagicMock(return_value=[])
    return client


@pytest.fixture
def mock_storage_with_metadata():
    """Minio client that returns one metadata.json object with sample data."""
    client = MagicMock()
    obj = MagicMock()
    obj.object_name = "2025/01/sample-1/metadata.json"
    client.list_objects = MagicMock(return_value=[obj])
    response = MagicMock()
    response.read = MagicMock(
        return_value=b'{"miners": {"0xabc": {"slug": "miner1", "evaluation": {"generated_wins": True}}}}'
    )
    response.close = MagicMock()
    response.release_conn = MagicMock()
    client.get_object = MagicMock(return_value=response)
    return client


@pytest.fixture
def mock_openai_client():
    """AsyncOpenAI client with mocked chat completions."""
    client = AsyncMock()
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = "A natural voice with clear tone."
    client.chat.completions.create = AsyncMock(return_value=completion)
    return client


@pytest.fixture
def sample_commitment_dict() -> Dict[str, Any]:
    """Valid commitment dict for chain/validation tests."""
    return {
        "model_name": "user/tts-model",
        "model_revision": "abc123def456",
        "chute_id": "chute-uuid-here",
    }
