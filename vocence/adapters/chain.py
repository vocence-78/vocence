"""
Bittensor chain utilities for Vocence.

Provides functions for parsing miner commitments and interacting with the chain.

Two commitment formats are supported:

* Legacy JSON: ``{"model_name", "model_revision", "chute_id"}`` (live-endpoint era).
* v7 reveal: ``v7|<repo>|sha256:<digest>`` — the content-addressed, model-submission
  format used by the redesigned subnet. The commitment binds a hotkey to the exact
  content hash of the uploaded model, so weights cannot be swapped after commit.
"""

import json
import re
from typing import Any, Dict, Tuple, Optional

REVEAL_VERSION = "v7"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def format_reveal(repo: str, digest: str) -> str:
    """Build a v7 reveal string binding a repo to an immutable content digest.

    Args:
        repo: Model repository, e.g. ``ns/vocence-prompttts-v1``.
        digest: Content digest, ``sha256:<64 hex>``.

    Returns:
        ``v7|<repo>|<digest>`` suitable for ``set_reveal_commitment``.

    Raises:
        ValueError: if repo is empty, contains ``|``, or the digest is malformed.
    """
    repo = (repo or "").strip()
    digest = (digest or "").strip().lower()
    if not repo or "|" in repo:
        raise ValueError(f"invalid repo for reveal: {repo!r}")
    if not _DIGEST_RE.match(digest):
        raise ValueError(f"invalid digest for reveal (want sha256:<64hex>): {digest!r}")
    return f"{REVEAL_VERSION}|{repo}|{digest}"


def parse_reveal(commit_value: str) -> Dict[str, Any]:
    """Parse a v7 reveal commitment.

    Args:
        commit_value: Raw on-chain commitment value.

    Returns:
        ``{"version", "repo", "digest"}`` for a well-formed v7 reveal, else ``{}``.
        Malformed or non-v7 values return ``{}`` (never raises) so callers can treat
        them as "no valid submission".
    """
    if not commit_value or not isinstance(commit_value, str):
        return {}
    parts = commit_value.strip().split("|")
    if len(parts) != 3:
        return {}
    version, repo, digest = (p.strip() for p in parts)
    if version != REVEAL_VERSION:
        return {}
    repo = repo
    digest = digest.lower()
    if not repo or not _DIGEST_RE.match(digest):
        return {}
    return {"version": version, "repo": repo, "digest": digest}


def immutable_ref(repo: str, digest: str) -> str:
    """Canonical ``repo@sha256:digest`` content reference."""
    return f"{repo}@{digest.strip().lower()}"

