"""Tests for vocence.adapters.deployment commit_command."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vocence.adapters.deployment import commit_command


def _mock_wallet():
    """Wallet mock with hotkey.ss58_address for emit_log slice."""
    wallet = MagicMock()
    wallet.hotkey.ss58_address = "5GrwvaEF5zXb26Fz9rcQpDWS57T"
    return wallet


@pytest.fixture
def mock_bt():
    """Mock bittensor module so commit_command runs without real wallet/network."""
    with patch("bittensor.Wallet", return_value=_mock_wallet()) as m_wallet_cls:
        mock_subtensor_instance = MagicMock()
        mock_subtensor_instance.set_reveal_commitment = AsyncMock(return_value=None)
        mock_async_subtensor = MagicMock(return_value=mock_subtensor_instance)
        with patch("bittensor.AsyncSubtensor", mock_async_subtensor):
            yield {
                "Wallet": m_wallet_cls,
                "AsyncSubtensor": mock_async_subtensor,
                "set_reveal_commitment": mock_subtensor_instance.set_reveal_commitment,
            }


@pytest.fixture
def mock_emit_log():
    """Suppress logging in tests."""
    with patch("vocence.adapters.deployment.emit_log"):
        yield


class TestCommitCommand:
    """Tests for commit_command."""

    @pytest.mark.asyncio
    async def test_success_returns_result_dict(self, mock_bt, mock_emit_log):
        result = await commit_command(
            model_name="user/tts-model",
            model_revision="abc123def456789",
            chute_id="chute-uuid",
        )
        assert result["success"] is True
        assert result["model_name"] == "user/tts-model"
        assert result["model_revision"] == "abc123def456789"
        assert result["chute_id"] == "chute-uuid"

    @pytest.mark.asyncio
    async def test_success_calls_set_reveal_commitment_with_expected_data(
        self, mock_bt, mock_emit_log
    ):
        await commit_command(
            model_name="repo/model",
            model_revision="sha123",
            chute_id="chute-1",
        )
        mock_bt["set_reveal_commitment"].assert_called_once()
        call_kw = mock_bt["set_reveal_commitment"].call_args.kwargs
        assert call_kw["data"] == json.dumps({
            "model_name": "repo/model",
            "model_revision": "sha123",
            "chute_id": "chute-1",
        })
        assert call_kw["blocks_until_reveal"] == 1
        assert "wallet" in call_kw
        assert "netuid" in call_kw

    @pytest.mark.asyncio
    async def test_uses_default_coldkey_hotkey_from_config(self, mock_bt, mock_emit_log):
        with patch("vocence.adapters.deployment.COLDKEY_NAME", "default_cold"):
            with patch("vocence.adapters.deployment.HOTKEY_NAME", "default_hot"):
                await commit_command(
                    model_name="m",
                    model_revision="r",
                    chute_id="c",
                )
        mock_bt["Wallet"].assert_called_once_with(name="default_cold", hotkey="default_hot")

    @pytest.mark.asyncio
    async def test_uses_provided_coldkey_hotkey(self, mock_bt, mock_emit_log):
        await commit_command(
            model_name="m",
            model_revision="r",
            chute_id="c",
            coldkey="my_cold",
            hotkey="my_hot",
        )
        mock_bt["Wallet"].assert_called_once_with(name="my_cold", hotkey="my_hot")

    @pytest.mark.asyncio
    async def test_retry_on_generic_error_then_succeed(self, mock_bt, mock_emit_log):
        set_commit = mock_bt["set_reveal_commitment"]
        set_commit.side_effect = [Exception("network blip"), None]
        with patch("vocence.adapters.deployment.asyncio.sleep", new_callable=AsyncMock):
            result = await commit_command(
                model_name="m",
                model_revision="r",
                chute_id="c",
            )
        assert result["success"] is True
        assert set_commit.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_space_limit_exceeded_then_succeed(self, mock_bt, mock_emit_log):
        set_commit = mock_bt["set_reveal_commitment"]
        set_commit.side_effect = [Exception("SpaceLimitExceeded"), None]
        with patch("vocence.adapters.deployment.asyncio.sleep", new_callable=AsyncMock):
            result = await commit_command(
                model_name="m",
                model_revision="r",
                chute_id="c",
            )
        assert result["success"] is True
        assert set_commit.call_count == 2

    @pytest.mark.asyncio
    async def test_failure_after_max_retries_returns_error_dict(self, mock_bt, mock_emit_log):
        mock_bt["set_reveal_commitment"].side_effect = Exception("permanent failure")
        with patch("vocence.adapters.deployment.asyncio.sleep", new_callable=AsyncMock):
            result = await commit_command(
                model_name="m",
                model_revision="r",
                chute_id="c",
            )
        assert result["success"] is False
        assert "error" in result
        assert "permanent failure" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_in_commit_returns_error_dict(self, mock_bt, mock_emit_log):
        mock_bt["set_reveal_commitment"].side_effect = RuntimeError("wallet error")

        result = await commit_command(
            model_name="m",
            model_revision="r",
            chute_id="c",
        )
        assert result["success"] is False
        assert result["error"] == "wallet error"

    @pytest.mark.asyncio
    async def test_short_revision_slice_safe(self, mock_bt, mock_emit_log):
        result = await commit_command(
            model_name="m",
            model_revision="short",
            chute_id="c",
        )
        assert result["success"] is True
        assert result["model_revision"] == "short"
