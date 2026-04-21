"""Tests for vocence.ranking.calculator."""
import pytest
from unittest.mock import MagicMock
from vocence.ranking.calculator import calculate_scores_from_storage, calculate_scores_from_samples

@pytest.mark.asyncio
async def test_empty_bucket_returns_empty_dict(mock_minio_client):
    mock_minio_client.list_objects = MagicMock(return_value=[])
    result = await calculate_scores_from_storage(mock_minio_client)
    assert result == {}

@pytest.mark.asyncio
async def test_single_metadata_aggregates(mock_storage_with_metadata):
    result = await calculate_scores_from_storage(mock_storage_with_metadata)
    assert isinstance(result, dict)
    if result:
        for data in result.values():
            assert "wins" in data and "total" in data and "win_rate" in data

@pytest.mark.asyncio
async def test_calculate_scores_from_samples_delegates(mock_minio_client):
    result = await calculate_scores_from_samples(mock_minio_client)
    assert result == {}


@pytest.mark.asyncio
async def test_calculate_scores_from_storage_uses_explicit_bucket(mock_storage_with_metadata):
    await calculate_scores_from_storage(mock_storage_with_metadata, bucket_name="other-bucket")
    mock_storage_with_metadata.list_objects.assert_called_once_with("other-bucket", recursive=True)
    mock_storage_with_metadata.get_object.assert_called_once_with("other-bucket", "2025/01/sample-1/metadata.json")
