"""
Chutes deployment API client for Vocence.

Provides functions for resolving chute IDs to endpoints and calling miner TTS models.
"""

import asyncio
import time
from typing import Any, Dict

import aiohttp

from vocence.domain.config import CHUTES_BASE_URL, CHUTES_AUTH_KEY, CHUTE_INFO_CACHE_TTL
from vocence.shared.logging import emit_log

# Cache for resolved chute info: chute_id -> (info_dict, cached_at)
_chute_cache: Dict[str, tuple[Dict[str, Any], float]] = {}


async def fetch_chute_details(
    session: aiohttp.ClientSession,
    chute_id: str,
) -> Dict[str, Any] | None:
    """Get chute info from Chutes API by chute_id.
    
    Args:
        session: aiohttp client session
        chute_id: Chutes deployment ID
        
    Returns:
        Chute info dict with 'slug', 'hot' status, etc. or None if failed
    """
    if chute_id in _chute_cache:
        info, cached_at = _chute_cache[chute_id]
        if time.time() - cached_at < CHUTE_INFO_CACHE_TTL:
            return info
    
    try:
        url = f"{CHUTES_BASE_URL}/chutes/{chute_id}"
        headers = {}
        if CHUTES_AUTH_KEY:
            headers["Authorization"] = f"Bearer {CHUTES_AUTH_KEY}"
        
        async with session.get(
            url,
            headers=headers or None,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                emit_log(f"Chutes API error for {chute_id}: {resp.status} {body[:200]}", "warn")
                return None
            
            info = await resp.json()
            _chute_cache[chute_id] = (info, time.time())
            return info
            
    except asyncio.TimeoutError:
        emit_log(f"Timeout fetching chute info: {chute_id}", "warn")
        return None
    except Exception as e:
        emit_log(f"Error fetching chute {chute_id}: {e}", "warn")
        return None


async def fetch_chute_code(
    session: aiohttp.ClientSession,
    chute_id: str,
) -> str | None:
    """Fetch the deployed chute's Python source (deploy script) from Chutes API.

    Used by the owner for wrapper integrity: mask approved vars, normalize, hash, compare to canonical.

    Args:
        session: aiohttp client session
        chute_id: Chutes deployment ID

    Returns:
        Raw deploy script source string, or None if fetch failed
    """
    try:
        url = f"{CHUTES_BASE_URL}/chutes/code/{chute_id}"
        headers = {}
        if CHUTES_AUTH_KEY:
            headers["Authorization"] = f"Bearer {CHUTES_AUTH_KEY}"
        async with session.get(
            url,
            headers=headers or None,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                emit_log(f"Chutes code API error for {chute_id}: {resp.status} {body[:200]}", "warn")
                return None
            return await resp.text()
    except asyncio.TimeoutError:
        emit_log(f"Timeout fetching chute code: {chute_id}", "warn")
        return None
    except Exception as e:
        emit_log(f"Error fetching chute code {chute_id}: {e}", "warn")
        return None


# Chutes TTS endpoints: /speak (VocenceSpeakRequest: text, instruction) is the common API for deployed chutes
CHUTE_TTS_PATH = "/speak"


def construct_chute_endpoint(slug: str) -> str:
    """Build the TTS endpoint URL from chute slug (POST /speak with text, instruction).
    
    Args:
        slug: Chute slug (e.g., "victor359-qwen3-tts-voicedesign")
        
    Returns:
        Full endpoint URL, e.g. https://{slug}.chutes.ai/speak
    """
    return f"https://{slug}.chutes.ai{CHUTE_TTS_PATH}"

