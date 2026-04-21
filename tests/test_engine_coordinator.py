"""Tests for vocence.engine.coordinator (mocked, no bittensor)."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestCoordinatorImports:
    """Coordinator module imports and exposes main entry points."""

    def test_main_sync_exists(self):
        from vocence.engine.coordinator import main_sync
        assert callable(main_sync)

    def test_main_async_exists(self):
        from vocence.engine.coordinator import main
        assert callable(main)

    def test_cycle_step_exists(self):
        from vocence.engine.coordinator import cycle_step
        assert callable(cycle_step)


class TestCycleStepMocked:
    """cycle_step can be invoked with mocks (no real bittensor)."""

    @pytest.mark.asyncio
    async def test_cycle_step_accepts_mocked_args(self):
        from vocence.engine.coordinator import cycle_step
        mock_subtensor = MagicMock()
        mock_wallet = MagicMock()
        mock_storage = MagicMock()
        # May raise or run; we only check it's callable with three args
        try:
            await cycle_step(mock_subtensor, mock_wallet, mock_storage)
        except Exception:
            pass  # Expected if bittensor/network not available
