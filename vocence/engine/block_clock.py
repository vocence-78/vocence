"""
Shared chain-block clock.

A single poller is the only caller of get_current_block; it publishes the latest
block to an in-process cache that every other task (generator, weight-setter,
registry) reads. This collapses several independent block-polling loops into one
RPC source per process and avoids subtensor rate-limit pressure.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from vocence.shared.logging import emit_log
from vocence.domain.config import SUBTENSOR_TIMEOUT_SEC

SECONDS_PER_BLOCK = 12
# Consider the cached block stale if the poller hasn't refreshed it within this window
# (one failed poll ~ SUBTENSOR_TIMEOUT_SEC, plus a couple of block-times of slack).
_STALE_AFTER_SEC = SUBTENSOR_TIMEOUT_SEC + SECONDS_PER_BLOCK * 2


class BlockClock:
    """In-process cache of the current chain block, updated by one poller."""

    def __init__(self) -> None:
        self._block: Optional[int] = None
        self._updated_at: Optional[float] = None

    def set(self, block: int) -> None:
        self._block = block
        self._updated_at = time.monotonic()

    def get(self) -> Optional[int]:
        return self._block

    async def get_async(self) -> int:
        """Return the cached block; raise if unset or stale so callers don't act on a
        block the poller stopped refreshing (they wait/retry instead)."""
        if self._block is None:
            raise RuntimeError("block clock not ready")
        if self._updated_at is None or (time.monotonic() - self._updated_at) > _STALE_AFTER_SEC:
            raise RuntimeError("block clock stale")
        return self._block


async def run_block_poller(
    subtensor_ref: dict,
    clock: BlockClock,
    reconnect: Callable[[dict], Awaitable[None]],
) -> None:
    """Poll get_current_block every block-time, publish to the clock, own reconnect."""
    while True:
        try:
            b = await asyncio.wait_for(
                subtensor_ref["client"].get_current_block(), timeout=SUBTENSOR_TIMEOUT_SEC
            )
            clock.set(b)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            emit_log(f"block poller: get_current_block failed ({e}); reconnecting subtensor", "warn")
            try:
                await reconnect(subtensor_ref)
            except Exception:
                pass
        await asyncio.sleep(SECONDS_PER_BLOCK)
