"""
Miner validation utilities for Vocence.

Provides functions for validating miners, fetching model hashes from HuggingFace,
and detecting duplicate/plagiarized models.
"""

import os
import asyncio
import hashlib
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from huggingface_hub import HfApi

from vocence.domain.config import (
    HF_AUTH_TOKEN,
    MODEL_FINGERPRINT_CACHE_TTL,
    BASE_MODEL_CHUTE_ID,
    BASE_MODEL_MODEL_NAME,
    BASE_MODEL_MODEL_REVISION,
    BASE_MODEL_WEIGHTS_HASH,
)
from vocence.shared.logging import emit_log
from vocence.domain.entities import ParticipantInfo
from vocence.adapters.chutes import fetch_chute_details, fetch_chute_code
from vocence.registry.wrapper_integrity import check_wrapper_integrity

# Chute name must contain this substring (case-insensitive) for owner validation to pass.
# Checked against the chute name from Chutes API (e.g. vocence-parler-tts-010), not chute_id (UUID).
CHUTE_NAME_MAGIC_WORD = "vocence"

# Weight file extensions for TTS models (include common formats)
WEIGHT_EXTENSIONS = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx")

# Cache for model hashes: (model, revision) -> ((hash, actual_revision), cached_at)
_model_hash_cache: Dict[Tuple[str, str], Tuple[Optional[Tuple[str, str]], float]] = {}

# Cache for API blacklist
_api_blacklist_cache: Tuple[Set[str], float] = (set(), 0)
_API_BLACKLIST_CACHE_TTL = 300  # 5 minutes


def load_blacklist() -> set:
    """Load blacklisted hotkeys from the centralized API.
    
    Uses caching to avoid frequent API calls.
    
    Returns:
        Set of blacklisted hotkey addresses
    """
    global _api_blacklist_cache
    
    now = time.time()
    cached_blacklist, cached_at = _api_blacklist_cache
    
    # Return cached value if still valid
    if cached_blacklist and (now - cached_at) < _API_BLACKLIST_CACHE_TTL:
        return cached_blacklist
    
    try:
        # Run async fetch in sync context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't run async from sync in running loop, return cached
                return cached_blacklist
        except RuntimeError:
            pass
        
        blacklist = asyncio.run(_fetch_blacklist_from_api())
        _api_blacklist_cache = (blacklist, now)
        return blacklist
    except Exception as e:
        emit_log(f"Failed to fetch blacklist from API: {e}", "warn")
        return cached_blacklist  # Return stale cache on error


async def _fetch_blacklist_from_api() -> set:
    """Async fetch blacklist from API.
    
    Returns:
        Set of blacklisted miner hotkeys
    """
    from vocence.domain.config import API_URL as api_url
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{api_url}/blocklist/participants",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    hotkeys = await response.json()
                    return set(hotkeys)
                else:
                    emit_log(f"Blacklist API returned {response.status}", "warn")
                    return set()
        except Exception as e:
            emit_log(f"Blacklist API request failed: {e}", "warn")
            return set()


async def get_model_fingerprint(model_id: str, revision: str) -> Optional[Tuple[str, str]]:
    """Get model hash and actual revision from HuggingFace.
    
    Computes a hash from all weight file SHA256s in the model repository.
    Includes .safetensors, .bin, .pt, .pth, .ckpt files.
    
    Args:
        model_id: HuggingFace model repo (e.g., "user/model-name")
        revision: Git commit hash
        
    Returns:
        Tuple of (model_hash, actual_revision) or None if failed
    """
    key = (model_id, revision)
    now = time.time()
    
    if key in _model_hash_cache:
        cached, cached_at = _model_hash_cache[key]
        if now - cached_at < MODEL_FINGERPRINT_CACHE_TTL:
            return cached
    
    def _fetch_repo_info(token):
        return HfApi(token=token).repo_info(
            repo_id=model_id,
            repo_type="model",
            revision=revision,
            files_metadata=True,
        )
    
    def _hash_from_info(info):
        actual_revision = getattr(info, "sha", None)
        siblings = getattr(info, "siblings", None) or []
        def _get_filename(s):
            return getattr(s, "rfilename", None) or getattr(s, "path", "") or ""
        def _get_lfs_sha256(lfs_info):
            if lfs_info is None:
                return None
            if isinstance(lfs_info, dict):
                return lfs_info.get("sha256") or lfs_info.get("oid")
            return getattr(lfs_info, "sha256", None) or getattr(lfs_info, "oid", None)
        shas = set()
        for sibling in siblings:
            filename = _get_filename(sibling)
            lfs_hash = _get_lfs_sha256(getattr(sibling, "lfs", None))
            if not lfs_hash or not any(filename.endswith(ext) for ext in WEIGHT_EXTENSIONS):
                continue
            shas.add(str(lfs_hash))
        if not actual_revision:
            return None, False
        if not shas:
            return (hashlib.sha256(actual_revision.encode()).hexdigest(), actual_revision), True
        return (hashlib.sha256("".join(sorted(shas)).encode()).hexdigest(), actual_revision), False
    
    try:
        info = await asyncio.to_thread(_fetch_repo_info, HF_AUTH_TOKEN or None)
        result, used_revision_fallback = _hash_from_info(info)
        if result is None:
            emit_log(f"No revision (sha) in model info for {model_id}@{revision}", "warn")
            _model_hash_cache[key] = (None, now)
            return None
        if used_revision_fallback:
            emit_log(f"No weight-file LFS hashes for {model_id}@{revision}; using revision-based fingerprint", "info")
        _model_hash_cache[key] = (result, now)
        return result
        
    except Exception as e:
        err_msg = str(e)
        if HF_AUTH_TOKEN and ("401" in err_msg or "Invalid username or password" in err_msg or "Invalid user token" in err_msg):
            try:
                emit_log(f"Retrying without HF token for {model_id}@{revision} (public repo)", "info")
                info = await asyncio.to_thread(_fetch_repo_info, None)
                result, _ = _hash_from_info(info)
                if result is not None:
                    _model_hash_cache[key] = (result, now)
                    return result
            except Exception as retry_e:
                emit_log(f"Retry without token failed for {model_id}@{revision}: {retry_e}", "warn")
        emit_log(f"Failed to fetch model info for {model_id}@{revision}: {type(e).__name__}: {e}", "warn")
        _model_hash_cache[key] = (None, now)
        return None


async def validate_miner(
    session: aiohttp.ClientSession,
    uid: int,
    hotkey: str,
    model_name: str,
    model_revision: str,
    chute_id: str,
    block: int,
) -> ParticipantInfo:
    """Validate a single miner.
    
    Validation steps:
    1. Fetch chute info from Chutes API
    2. Check chute is running (hot)
    3. Fetch model info from HuggingFace
    4. Compute model hash for plagiarism detection
    
    Args:
        session: aiohttp client session
        uid: Miner UID
        hotkey: Miner hotkey address
        model_name: HuggingFace model repo (e.g., "user/repo")
        model_revision: Git commit hash
        chute_id: Chutes deployment UUID
        block: Block when miner committed
        
    Returns:
        ParticipantInfo with validation result
    """
    info = ParticipantInfo(
        uid=uid,
        hotkey=hotkey,
        model_name=model_name,
        model_revision=model_revision,
        chute_id=chute_id,
        block=block,
    )

    # Owner base-model chute: skip chute/wrapper checks. The HF repo has no weight files so
    # get_model_fingerprint would fall back to a revision-based hash; pin BASE_MODEL_WEIGHTS_HASH
    # instead so detect_duplicates catches miners copying the base model.
    if chute_id == BASE_MODEL_CHUTE_ID:
        info.is_valid = True
        info.model_hash = BASE_MODEL_WEIGHTS_HASH
        emit_log(f"uid {uid} ({hotkey[:12]}...): base-model chute (BASE_MODEL_CHUTE_ID), always valid, pinned hash", "success")
        return info

    # Step 1: Fetch chute info (need slug to check magic word)
    chute = await fetch_chute_details(session, chute_id)
    if not chute:
        info.invalid_reason = "chute_fetch_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at chute_fetch", "warn")
        return info

    info.chute_slug = chute.get("slug", "")
    # Chute API may return "name" (display name) and/or "slug"; we check the name for the magic word
    chute_name = chute.get("name") or chute.get("slug") or ""

    # Step 1a: Chute name must contain the magic word "vocence" (e.g. vocence-parler-tts-010)
    if CHUTE_NAME_MAGIC_WORD not in (chute_name or "").lower():
        info.invalid_reason = "chute_name_missing_vocence"
        emit_log(
            f"uid {uid} ({hotkey[:12]}...): chute name must contain '{CHUTE_NAME_MAGIC_WORD}' (got {(chute_name or '')[:40]}...)",
            "warn",
        )
        return info
    # API may return hot: true or status: "hot"
    is_hot = chute.get("hot", False) or (chute.get("status") or "").lower() == "hot"
    info.chute_status = "hot" if is_hot else "cold"

    # Step 1b: Owner-side wrapper integrity — fetch deploy script from Chutes, hash vs canonical
    deployed_code = await fetch_chute_code(session, chute_id)
    if not deployed_code:
        info.invalid_reason = "chute_code_fetch_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at chute_code_fetch", "warn")
        return info
    valid_wrapper, wrapper_reason = check_wrapper_integrity(deployed_code)
    if not valid_wrapper:
        info.invalid_reason = wrapper_reason or "wrapper_hash_mismatch"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at wrapper_integrity ({wrapper_reason or 'wrapper_hash_mismatch'})", "warn")
        return info

    # Step 2: Check chute is running
    if not is_hot:
        info.invalid_reason = "chute_not_running"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at chute_hot (not running)", "warn")
        return info

    # Step 3: Verify revision matches chute
    chute_revision = chute.get("revision", "")
    if chute_revision and model_revision != chute_revision:
        info.invalid_reason = f"revision_mismatch:chute={chute_revision}"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at revision_chute_match", "warn")
        return info

    # Step 4: Fetch model info from HuggingFace
    model_info = await get_model_fingerprint(model_name, model_revision)
    if not model_info:
        info.invalid_reason = "hf_model_fetch_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at model_fingerprint", "warn")
        return info

    model_hash, hf_revision = model_info
    # If a miner commits the owner base-model HF repo (same name + revision) from their own chute,
    # pin the canonical weights hash so detect_duplicates groups them with the owner.
    if model_name == BASE_MODEL_MODEL_NAME and model_revision == BASE_MODEL_MODEL_REVISION:
        info.model_hash = BASE_MODEL_WEIGHTS_HASH
    else:
        info.model_hash = model_hash

    # Step 5: Verify revision matches HuggingFace
    if model_revision != hf_revision:
        info.invalid_reason = f"revision_mismatch:hf={hf_revision}"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at revision_hf_match", "warn")
        return info

    info.is_valid = True
    emit_log(
        f"uid {uid} ({hotkey[:12]}...): passed chute_fetch, wrapper_integrity, chute_hot, revision_chute_match, model_fingerprint, revision_hf_match",
        "success",
    )
    return info


def detect_duplicates(miners: List[ParticipantInfo]) -> List[ParticipantInfo]:
    """Detect plagiarism by checking duplicate model hashes.
    
    For each unique model hash, only the miner with the earliest
    commit block is kept as valid. Later miners are marked as duplicates.
    
    Args:
        miners: List of validated miners
        
    Returns:
        Updated miners list with plagiarism detection applied
    """
    # Group valid miners by model hash
    hash_to_miners: Dict[str, List[Tuple[int, int, MinerDetails]]] = {}
    
    for miner in miners:
        if not miner.is_valid or not miner.model_hash:
            continue
        
        if miner.model_hash not in hash_to_miners:
            hash_to_miners[miner.model_hash] = []
        hash_to_miners[miner.model_hash].append((miner.block, miner.uid, miner))
    
    # Keep only earliest miner for each hash
    for model_hash, group in hash_to_miners.items():
        if len(group) <= 1:
            continue
        
        # Sort by block (earliest first), then by UID
        group.sort(key=lambda x: (x[0], x[1]))
        earliest_block, earliest_uid, _ = group[0]
        
        # Mark duplicates as invalid
        for block, uid, miner in group[1:]:
            if miner.is_valid:
                miner.is_valid = False
                miner.invalid_reason = f"duplicate_model:earliest_uid={earliest_uid}"
                emit_log(
                    f"Duplicate model detected: uid={uid} copied from uid={earliest_uid} "
                    f"(hash={model_hash[:16]}...)",
                    "warn"
                )
    
    return miners

