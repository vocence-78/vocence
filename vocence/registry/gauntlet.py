"""Deterministic validation gauntlet (spec-driven).

The ordered, reproducible checks every on-chain submission must pass before it is
eligible for evaluation. Every rule comes from ``vocence.toml`` (see
:mod:`vocence.domain.spec`), so any validator or third party reaches the same
verdict from public inputs — which is why the subnet needs no operator-run
blocklist: an invalid model simply fails a public check.

This module is intentionally pure: it operates on already-fetched data (file list,
``config.json`` dicts, the canonical-script hash, a precomputed max fingerprint
similarity). Fetching from Hippius lives in :mod:`vocence.adapters.storage`; the
orchestration that fetches-then-checks lives in the registry driver. Keeping the
logic side-effect-free makes it trivially testable and identical across validators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from vocence.domain.spec import SubnetSpec


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    ok: bool
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GauntletResult:
    repo: str
    digest: str
    checks: List[CheckOutcome] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def first_failure(self) -> Optional[CheckOutcome]:
        return next((c for c in self.checks if not c.ok), None)


# --------------------------------------------------------------------------- checks
def check_repo_pattern(repo: str, spec: SubnetSpec) -> CheckOutcome:
    ok = bool(re.match(spec.repo_pattern, repo or ""))
    return CheckOutcome(
        "repo_pattern", ok,
        "" if ok else f"repo {repo!r} does not match {spec.repo_pattern!r}",
    )


def check_file_manifest(
    files: Sequence[str], spec: SubnetSpec, *, miner_py_sha256: Optional[str] = None
) -> CheckOutcome:
    """Strict allowlist: all required present, no unexpected extras, no rogue *.py.

    ``miner_py_sha256`` (if provided) must equal the canonical hash — the single
    permitted Python file is the locked inference script.
    """
    fileset = set(files)
    allowed = set(spec.required_files) | set(spec.allowed_files)

    missing = [f for f in spec.required_files if f not in fileset]
    if missing:
        return CheckOutcome("file_manifest", False,
                            f"missing required files: {sorted(missing)}",
                            {"missing": sorted(missing)})

    if spec.require_safetensors and not any(f.endswith(".safetensors") for f in fileset):
        return CheckOutcome("file_manifest", False, "no *.safetensors present")

    # No *.py except the single permitted (hash-locked) inference script.
    # Checked before the generic extras check so a rogue script gets the specific reason.
    rogue_py = [f for f in fileset if f.endswith(".py") and f != spec.forbidden_py_except]
    if rogue_py:
        return CheckOutcome("file_manifest", False,
                            f"forbidden python files: {sorted(rogue_py)}",
                            {"rogue_py": sorted(rogue_py)})

    extras = [f for f in fileset if f not in allowed]
    if extras:
        return CheckOutcome("file_manifest", False,
                            f"unexpected extra files: {sorted(extras)}",
                            {"extras": sorted(extras)})

    if spec.forbidden_py_except and spec.canonical_miner_py_sha256:
        if miner_py_sha256 is None:
            return CheckOutcome("file_manifest", False,
                                f"{spec.forbidden_py_except} hash not provided for verification")
        if miner_py_sha256.lower() != spec.canonical_miner_py_sha256.lower():
            return CheckOutcome("file_manifest", False,
                                f"{spec.forbidden_py_except} sha256 mismatch (not the canonical script)",
                                {"expected": spec.canonical_miner_py_sha256,
                                 "actual": miner_py_sha256})

    return CheckOutcome("file_manifest", True, details={"file_count": len(fileset)})


def check_architecture(
    candidate_cfg: Dict[str, Any], seed_cfg: Dict[str, Any], spec: SubnetSpec
) -> CheckOutcome:
    """config.json must match the seed on every arch-lock key; no remote code / quant."""
    if "auto_map" in candidate_cfg:
        return CheckOutcome("architecture", False, "config.json must not contain 'auto_map' (no remote code)")
    if "quantization_config" in candidate_cfg:
        return CheckOutcome("architecture", False, "config.json must not contain 'quantization_config' (no quantized models)")

    if candidate_cfg.get("architectures") != seed_cfg.get("architectures"):
        return CheckOutcome("architecture", False,
                            f"architectures mismatch: seed={seed_cfg.get('architectures')!r} "
                            f"candidate={candidate_cfg.get('architectures')!r}")
    for key in spec.arch_lock_keys:
        if candidate_cfg.get(key) != seed_cfg.get(key):
            return CheckOutcome("architecture", False,
                                f"lock key {key!r} mismatch: seed={seed_cfg.get(key)!r} "
                                f"candidate={candidate_cfg.get(key)!r}")
    return CheckOutcome("architecture", True,
                        details={"model_type": candidate_cfg.get("model_type")})


def check_duplicate(max_similarity: Optional[float], spec: SubnetSpec) -> CheckOutcome:
    """Reject near-duplicates. ``max_similarity`` is the highest fingerprint similarity
    to any already-accepted model (None → no corpus / first model)."""
    if max_similarity is None:
        return CheckOutcome("duplicate", True, details={"max_similarity": None})
    ok = max_similarity < spec.similarity_threshold
    return CheckOutcome(
        "duplicate", ok,
        "" if ok else f"near-duplicate: similarity {max_similarity:.3f} >= {spec.similarity_threshold}",
        {"max_similarity": max_similarity, "threshold": spec.similarity_threshold},
    )


# --------------------------------------------------------------------------- driver
def run_gauntlet(
    *,
    repo: str,
    digest: str,
    files: Sequence[str],
    candidate_cfg: Dict[str, Any],
    seed_cfg: Dict[str, Any],
    spec: SubnetSpec,
    miner_py_sha256: Optional[str] = None,
    max_similarity: Optional[float] = None,
) -> GauntletResult:
    """Run checks in order, short-circuiting on the first failure (cheapest first)."""
    result = GauntletResult(repo=repo, digest=digest)

    result.checks.append(check_repo_pattern(repo, spec))
    if not result.valid:
        return result

    result.checks.append(check_file_manifest(files, spec, miner_py_sha256=miner_py_sha256))
    if not result.valid:
        return result

    result.checks.append(check_architecture(candidate_cfg, seed_cfg, spec))
    if not result.valid:
        return result

    result.checks.append(check_duplicate(max_similarity, spec))
    return result
