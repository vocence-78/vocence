"""
Owner-side wrapper integrity check for Vocence chutes.

The owner (not validators) fetches the deployed chute's Python source from the Chutes API,
masks the four approved variables (VOCENCE_REPO, VOCENCE_REVISION, VOCENCE_CHUTES_USER, VOCENCE_CHUTE_ID),
normalizes both canonical and deployed source (AST dump), and compares hashes.
If fetch fails or hash mismatch, the participant is marked invalid.
"""

import ast
import hashlib
import re
from pathlib import Path
from typing import Tuple

# Approved variables miners may change; we mask their values before hashing
APPROVED_VAR_PATTERNS = [
    (re.compile(r'VOCENCE_REPO\s*=\s*["\'].*?["\']', re.DOTALL), 'VOCENCE_REPO = ""'),
    (re.compile(r'VOCENCE_REVISION\s*=\s*["\'].*?["\']', re.DOTALL), 'VOCENCE_REVISION = ""'),
    (re.compile(r'VOCENCE_CHUTES_USER\s*=\s*["\'].*?["\']', re.DOTALL), 'VOCENCE_CHUTES_USER = ""'),
    (re.compile(r'VOCENCE_CHUTE_ID\s*=\s*["\'].*?["\']', re.DOTALL), 'VOCENCE_CHUTE_ID = ""'),
]


def _load_canonical_source() -> str:
    """Load canonical wrapper source (template with empty placeholder values)."""
    template_path = Path(__file__).resolve().parent / "canonical_wrapper_template.jinja2"
    if not template_path.is_file():
        raise FileNotFoundError(f"Canonical template not found: {template_path}")
    text = template_path.read_text(encoding="utf-8")
    # Replace Jinja placeholders with empty string so masked form matches miner-deployed
    text = text.replace('"{{ huggingface_repository_name }}"', '""')
    text = text.replace('"{{ huggingface_repository_revision }}"', '""')
    text = text.replace('"{{ chute_username }}"', '""')
    text = text.replace('"{{ chute_name }}"', '""')
    return text


def _mask_approved_variables(source: str) -> str:
    """Replace approved variable values with empty string so hash ignores miner-specific values."""
    out = source
    for pattern, replacement in APPROVED_VAR_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _normalize_python(source: str) -> str:
    """Parse source as Python and return normalized AST dump (ignores formatting)."""
    try:
        tree = ast.parse(source)
        return ast.dump(tree, include_attributes=False)
    except SyntaxError:
        return ""


def _normalized_hash(source: str) -> str:
    """SHA-256 of normalized (masked) Python source."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def get_canonical_normalized_hash() -> str:
    """Return the hash of the canonical wrapper (masked + normalized). Used for comparison."""
    canonical = _load_canonical_source()
    masked = _mask_approved_variables(canonical)
    normalized = _normalize_python(masked)
    return _normalized_hash(normalized)


def check_wrapper_integrity(deployed_source: str) -> Tuple[bool, str | None]:
    """Compare deployed chute source to canonical wrapper.

    Args:
        deployed_source: Raw Python source from Chutes GET /chutes/code/{chute_id}

    Returns:
        (True, None) if hash matches canonical; (False, reason) otherwise.
    """
    if not deployed_source or not deployed_source.strip():
        return False, "empty_deploy_script"
    canonical_hash = get_canonical_normalized_hash()
    masked_deployed = _mask_approved_variables(deployed_source)
    normalized_deployed = _normalize_python(masked_deployed)
    if not normalized_deployed:
        return False, "deploy_script_syntax_error"
    deployed_hash = _normalized_hash(normalized_deployed)
    if deployed_hash != canonical_hash:
        return False, "wrapper_hash_mismatch"
    return True, None
