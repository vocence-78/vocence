"""
Per-validator local miner registry.

Each validator runs the *same* validation pipeline the owner API runs
(`ParticipantValidationTask._validate_participants`), but against a local SQLite DB
instead of the owner's Postgres. This removes the validator's dependency on the
owner API for the valid-miner list — validators read chain commitments, validate
miners themselves (HuggingFace + Chutes + duplicate detection), and persist results
locally.

- run_miner_registry()            -> supervised background loop (validate hourly)
- fetch_local_valid_participants() -> read valid miners from local SQLite

The blacklist stays centralized: it is fetched from the owner API and cached on disk
(fail to last-known on outage) so a brief API blip can't change the valid set.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import List

from vocence.shared.logging import emit_log
from vocence.domain.config import (
    REGISTRY_DB_PATH,
    BLOCKLIST_CACHE_PATH,
    PARTICIPANT_VALIDATION_INTERVAL,
    API_URL,
    COLDKEY_NAME,
    HOTKEY_NAME,
)
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


async def run_miner_registry() -> None:
    """Continuous loop: validate miners locally and persist to SQLite.

    Reuses the owner API's ParticipantValidationTask verbatim (same validation +
    dedup + owner injection), only the storage backend differs. The first pass runs
    on boot (after a small random delay so simultaneous auto-updates don't stampede
    HuggingFace/Chutes), then every PARTICIPANT_VALIDATION_INTERVAL with jitter.
    """
    await init_local_registry()
    from vocence.gateway.http.service.tasks.participant_validation import (
        ParticipantValidationTask,
    )

    task = ParticipantValidationTask()
    # Initial jitter (0-120s): avoid every validator validating at the same instant.
    await asyncio.sleep(random.random() * 120)
    emit_log("Local miner registry loop starting", "start")
    while True:
        try:
            await _sync_blacklist_to_local_db()
            await task._validate_participants()
        except asyncio.CancelledError:
            emit_log("Local miner registry cancelled", "warn")
            raise
        except Exception as e:
            emit_log(f"Local registry pass failed ({e}); retry next interval", "warn")
        await asyncio.sleep(PARTICIPANT_VALIDATION_INTERVAL * (1.0 + random.random() * 0.25))
