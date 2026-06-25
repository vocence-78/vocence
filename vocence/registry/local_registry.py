"""
Per-validator local miner registry: validators run the owner API's validation
pipeline locally against a SQLite DB instead of calling the API for valid miners.

- run_miner_registry()             -> supervised hourly validation loop
- fetch_local_valid_participants() -> read valid miners from local SQLite

The blacklist stays centralized; it's fetched and cached on disk (fail to last-known).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import List

from vocence.shared.logging import emit_log
from vocence.domain.config import (
    REGISTRY_DB_PATH,
    BLOCKLIST_CACHE_PATH,
    REGISTRY_VALIDATION_INTERVAL_BLOCKS,
    REGISTRY_VALIDATION_MAX_LAG_BLOCKS,
    CHAIN_NETWORK,
    SUBTENSOR_TIMEOUT_SEC,
    API_URL,
    COLDKEY_NAME,
    HOTKEY_NAME,
)
from vocence.engine.block_clock import SECONDS_PER_BLOCK
from vocence.domain.entities import ParticipantInfo

_initialized = False
_init_lock = asyncio.Lock()


async def init_local_registry() -> None:
    """Open the local SQLite engine and create tables (idempotent)."""
    global _initialized
    if _initialized:
        return
    async with _init_lock:
        if _initialized:
            return
        from vocence.registry.persistence.connection import (
            establish_connection,
            initialize_schema,
        )
        os.makedirs(os.path.dirname(REGISTRY_DB_PATH) or ".", exist_ok=True)
        dsn = f"sqlite+aiosqlite:///{REGISTRY_DB_PATH}"
        await establish_connection(dsn)
        await initialize_schema()
        _initialized = True
        emit_log(f"Local miner registry ready (sqlite: {REGISTRY_DB_PATH})", "success")


def _miner_to_participant(m) -> ParticipantInfo:
    """Map a RegisteredMiner ORM row to the ParticipantInfo the validator consumes."""
    return ParticipantInfo(
        uid=m.uid,
        hotkey=m.miner_hotkey,
        model_name=m.model_name or "",
        model_revision=m.model_revision or "",
        chute_id=m.chute_id or "",
        chute_slug=m.chute_slug or "",
        block=m.block or 0,
        is_valid=bool(m.is_valid),
        invalid_reason=m.invalid_reason,
        model_hash=m.model_hash or "",
    )


async def fetch_local_valid_participants() -> List[ParticipantInfo]:
    """Read valid miners from the local registry (same shape as the old API call)."""
    from vocence.registry.persistence.repositories.miner_repository import MinerRepository

    await init_local_registry()
    rows = await MinerRepository().fetch_valid_miners()
    return [_miner_to_participant(m) for m in rows]


# --- Centralized blacklist: fetch + disk cache (fail to last-known) ----------------


def _load_cached_blacklist() -> List[str]:
    try:
        with open(BLOCKLIST_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [str(h) for h in data] if isinstance(data, list) else []
    except Exception:
        return []


def _save_cached_blacklist(hotkeys: List[str]) -> None:
    os.makedirs(os.path.dirname(BLOCKLIST_CACHE_PATH) or ".", exist_ok=True)
    tmp = BLOCKLIST_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hotkeys, f)
    os.replace(tmp, BLOCKLIST_CACHE_PATH)


async def _fetch_blacklist_cached() -> List[str]:
    """Fetch the blacklist from the owner API; on failure use the last-known cache."""
    from vocence.adapters.api import create_service_client_from_wallet

    try:
        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME, hotkey_name=HOTKEY_NAME, api_url=API_URL
        )
        try:
            hotkeys = await client.get_blacklisted_miners()
        finally:
            await client.close()
        hotkeys = [str(h) for h in (hotkeys or [])]
        _save_cached_blacklist(hotkeys)
        return hotkeys
    except Exception as e:
        cached = _load_cached_blacklist()
        emit_log(
            f"Blacklist fetch failed ({e}); using {len(cached)} cached entries", "warn"
        )
        return cached


async def _sync_blacklist_to_local_db() -> None:
    """Mirror the (cached) central blacklist into the local blocked_entities table so
    the shared validation pipeline picks it up exactly as on the owner API."""
    from vocence.registry.persistence.repositories.blocklist_repository import (
        BlocklistRepository,
    )

    target = set(await _fetch_blacklist_cached())
    repo = BlocklistRepository()
    current = set(await repo.fetch_blocked_hotkeys())
    for hk in target - current:
        await repo.add_entry(hk, reason="central_blacklist")
    for hk in current - target:
        await repo.remove_entry(hk)


async def run_miner_registry(get_block=None, subtensor_ref=None) -> None:
    """Validate miners on chain-block boundaries, pinned to the boundary block.

    Every validator computes the same boundaries (block % INTERVAL == 0, offset 0) and
    validates commitments+metagraph pinned to that block, so independent validators
    read the identical snapshot and converge on the same valid-miner set. Reuses the
    owner API's ParticipantValidationTask verbatim (only the DB + snapshot block differ).

    get_block / subtensor_ref are injected by `serve` (shared block clock + connection).
    When run standalone they default to a private connection.
    """
    import bittensor as bt

    await init_local_registry()
    from vocence.gateway.http.service.tasks.participant_validation import (
        ParticipantValidationTask,
    )

    own_subtensor = subtensor_ref is None
    if own_subtensor:
        subtensor_ref = {"client": bt.AsyncSubtensor(network=CHAIN_NETWORK)}
    if get_block is None:
        async def get_block():
            return await asyncio.wait_for(
                subtensor_ref["client"].get_current_block(), timeout=SUBTENSOR_TIMEOUT_SEC
            )

    interval = REGISTRY_VALIDATION_INTERVAL_BLOCKS
    task = ParticipantValidationTask()
    last_boundary = None
    emit_log(
        f"Local miner registry loop starting (every {interval} blocks, snapshot-pinned, "
        f"max_lag={REGISTRY_VALIDATION_MAX_LAG_BLOCKS})",
        "start",
    )
    try:
        while True:
            try:
                block = await get_block()
            except asyncio.CancelledError:
                raise
            except Exception:
                block = None
            if block is None:
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue

            boundary = (block // interval) * interval
            if boundary != last_boundary:
                # Pin to the boundary when recent enough (synced across validators).
                # On first boot in a stale window, pin to current so we warm up
                # immediately and resync at the next boundary; otherwise skip (the
                # validator was down — don't query pruned state).
                if block - boundary <= REGISTRY_VALIDATION_MAX_LAG_BLOCKS:
                    pin = boundary
                elif last_boundary is None:
                    pin = block
                else:
                    pin = None
                if pin is not None:
                    try:
                        await _sync_blacklist_to_local_db()
                        await task._validate_participants(subtensor=subtensor_ref["client"], block=pin)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        emit_log(f"Local registry pass failed ({e}); retry", "warn")
                else:
                    emit_log(
                        f"Skipping stale validation boundary {boundary} (block {block})", "warn"
                    )
                last_boundary = boundary
            await asyncio.sleep(SECONDS_PER_BLOCK)
    finally:
        if own_subtensor:
            try:
                await subtensor_ref["client"].close()
            except Exception:
                pass
