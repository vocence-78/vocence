"""
Bittensor chain utilities for Vocence.

Provides functions for parsing miner commitments and interacting with the chain.
"""

import json
from typing import Any, Dict, List, Tuple, Optional


def _scale_compact_offset(data: bytes) -> int:
    """Length of a SCALE compact-encoded integer prefix at the start of `data`.

    Revealed commitments are stored as SCALE `Vec<u8>` (a compact length prefix
    followed by the raw payload bytes). Mirrors bittensor's own decode logic.
    """
    if not data:
        return 0
    mode = data[0] & 0b11
    if mode == 0:
        return 1
    if mode == 1:
        return 2
    if mode == 2:
        return 4
    # mode 3: big-integer compact — low 6 bits give (#bytes - 4), plus the mode byte.
    return 1 + (data[0] >> 2) + 4


def decode_revealed_commitment_value(raw: Any) -> str:
    """Decode one raw revealed-commitment value into its UTF-8 payload string.

    Robust to the two shapes subnet commitments come back as from the chain:
      - a hex string ("0x8d027b22...") — SCALE bytes, hex-encoded
      - an already-decoded str/bytes whose leading bytes are the SCALE length prefix
        (e.g. 'a\\x02{"model_name": ...}')

    bittensor 9.x's decoder assumed the first shape only and calls bytes.fromhex()
    unconditionally, which raises on the second shape (see get_all_revealed_commitments).
    """
    if isinstance(raw, (bytes, bytearray)):
        b = bytes(raw)
    elif isinstance(raw, str) and raw.startswith("0x"):
        try:
            b = bytes.fromhex(raw[2:])
        except ValueError:
            b = raw.encode("latin-1", errors="ignore")
    else:
        # latin-1 round-trips bytes 0-255 one-to-one, preserving the SCALE prefix.
        b = str(raw).encode("latin-1", errors="ignore")
    if not b:
        return ""
    return b[_scale_compact_offset(b):].decode("utf-8", errors="ignore").strip()


async def fetch_all_revealed_commitments(
    subtensor: Any, netuid: int, block: Optional[int] = None
) -> Dict[str, List[Tuple[int, str]]]:
    """Fetch revealed commitments for a subnet as {hotkey_ss58: [(block, payload_str), ...]}.

    Drop-in for AsyncSubtensor.get_all_revealed_commitments() that tolerates both
    on-chain commitment encodings (see decode_revealed_commitment_value). Queries the
    Commitments.RevealedCommitments storage map directly and decodes each entry.
    """
    substrate = subtensor.substrate
    block_hash = await substrate.get_block_hash(block) if block is not None else None
    query = await substrate.query_map(
        module="Commitments",
        storage_function="RevealedCommitments",
        params=[netuid],
        block_hash=block_hash,
    )
    result: Dict[str, List[Tuple[int, str]]] = {}
    async for key, value in query:
        hotkey = getattr(key, "value", key)
        entries = getattr(value, "value", value) or []
        decoded: List[Tuple[int, str]] = []
        for entry in entries:
            # Each entry is (raw_commitment, revealed_block).
            raw_commit, revealed_block = entry[0], entry[1]
            decoded.append((int(revealed_block), decode_revealed_commitment_value(raw_commit)))
        result[str(hotkey)] = decoded
    return result


def parse_commitment(commit_value: str) -> Dict[str, Any]:
    """Parse a miner's chain commitment.
    
    Expected format: {"model_name": "user/repo", "model_revision": "sha", "chute_id": "uuid"}
    
    Args:
        commit_value: The raw commitment value from the chain
        
    Returns:
        Parsed commitment data with model_name, model_revision, chute_id keys
    """
    if not commit_value:
        return {}
    try:
        parsed = json.loads(commit_value)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def validate_commitment_fields(commit: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate that commit has all required fields.
    
    Args:
        commit: Parsed commit dictionary
        
    Returns:
        Tuple of (is_valid, error_reason)
    """
    model_name = commit.get("model_name", "")
    model_revision = commit.get("model_revision", "")
    chute_id = commit.get("chute_id", "")
    
    if not model_name:
        return False, "missing_model_name"
    if not model_revision:
        return False, "missing_model_revision"
    if not chute_id:
        return False, "missing_chute_id"
    
    return True, None

