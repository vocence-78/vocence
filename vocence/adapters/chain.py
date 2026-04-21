"""
Bittensor chain utilities for Vocence.

Provides functions for parsing miner commitments and interacting with the chain.
"""

import json
from typing import Any, Dict, Tuple, Optional


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

