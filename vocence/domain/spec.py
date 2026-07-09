"""Subnet specification loader.

`vocence.toml` is the single source of truth for submission + evaluation rules
(the analog of Albedo's `chain.toml`). It is public and versioned so a miner can
reproduce every validation check locally before committing on chain, and so every
validator evaluates against identical, pinned rules.

Load it once with :func:`load_spec` (cached). The returned :class:`SubnetSpec` is
immutable.
"""

from __future__ import annotations

try:
    import tomllib  # Python >= 3.11
except ModuleNotFoundError:  # pragma: no cover - fallback for 3.10 dev envs
    import tomli as tomllib  # type: ignore
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

SPEC_FILENAME = "vocence.toml"


@dataclass(frozen=True)
class SubnetSpec:
    """Parsed, validated view of ``vocence.toml``."""

    # [chain]
    name: str
    netuid: int
    seed_repo: str
    seed_revision: str
    repo_pattern: str
    # [arch]
    arch_lock_keys: Tuple[str, ...]
    # [seed]
    seed_weights_hash: str
    # [files]
    required_files: Tuple[str, ...]
    allowed_files: Tuple[str, ...]
    require_safetensors: bool
    forbidden_py_except: str
    canonical_miner_py_sha256: str
    # [preeval]
    similarity_threshold: float
    # [incentive]
    win_margin: float
    court_size: int
    burn_uid: int
    margin_coefficient: float
    min_margin: float
    # [eval]
    corpus_min_samples: int
    intelligibility_max_wer: float
    adherence_questions_per_sample: int
    bootstrap_n: int
    bootstrap_alpha: float
    bootstrap_seed: int
    facet_weights: Dict[str, float]
    judges: Dict[str, str]

    def margin_for(self, king_composite: float) -> float:
        """Dynamic coronation margin: a fraction of the king's remaining headroom,
        floored at ``min_margin`` (see teutonic ``δ_t = c·king_loss``)."""
        return max(self.min_margin, self.margin_coefficient * (1.0 - float(king_composite)))

    def facet_weight(self, facet: str) -> float:
        return float(self.facet_weights.get(facet, 0.0))


def _spec_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    # Walk up from this file to find the repo-root vocence.toml.
    here = Path(__file__).resolve()
    for parent in [Path.cwd(), *here.parents]:
        candidate = parent / SPEC_FILENAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"{SPEC_FILENAME} not found (searched cwd and parents of {here})"
    )


def _build(raw: dict) -> SubnetSpec:
    chain = raw.get("chain", {})
    arch = raw.get("arch", {})
    seed = raw.get("seed", {})
    files = raw.get("files", {})
    preeval = raw.get("preeval", {})
    incentive = raw.get("incentive", {})
    ev = raw.get("eval", {})

    facet_weights = {str(k): float(v) for k, v in (ev.get("facet_weights", {}) or {}).items()}
    judges = {str(k): str(v) for k, v in (ev.get("judges", {}) or {}).items()}

    spec = SubnetSpec(
        name=str(chain["name"]),
        netuid=int(chain["netuid"]),
        seed_repo=str(chain["seed_repo"]),
        seed_revision=str(chain.get("seed_revision", "")),
        repo_pattern=str(chain["repo_pattern"]),
        arch_lock_keys=tuple(str(k) for k in arch.get("lock_keys", [])),
        seed_weights_hash=str(seed.get("seed_weights_hash", "")),
        required_files=tuple(str(f) for f in files.get("required", [])),
        allowed_files=tuple(str(f) for f in files.get("allowed", [])),
        require_safetensors=bool(files.get("require_safetensors", True)),
        forbidden_py_except=str(files.get("forbidden_py_except", "")),
        canonical_miner_py_sha256=str(files.get("canonical_miner_py_sha256", "")),
        similarity_threshold=float(preeval.get("similarity_threshold", 0.95)),
        win_margin=float(incentive.get("win_margin", 0.03)),
        court_size=int(incentive.get("court_size", 5)),
        burn_uid=int(incentive.get("burn_uid", 0)),
        margin_coefficient=float(incentive.get("margin_coefficient", 0.10)),
        min_margin=float(incentive.get("min_margin", 0.005)),
        corpus_min_samples=int(ev.get("corpus_min_samples", 128)),
        intelligibility_max_wer=float(ev.get("intelligibility_max_wer", 0.15)),
        adherence_questions_per_sample=int(ev.get("adherence_questions_per_sample", 50)),
        bootstrap_n=int(ev.get("bootstrap_n", 2000)),
        bootstrap_alpha=float(ev.get("bootstrap_alpha", 0.05)),
        bootstrap_seed=int(ev.get("bootstrap_seed", 3151662)),
        facet_weights=facet_weights,
        judges=judges,
    )
    _validate(spec)
    return spec


def _validate(spec: SubnetSpec) -> None:
    if spec.court_size < 1:
        raise ValueError("incentive.court_size must be >= 1")
    if not (0.0 < spec.win_margin < 1.0):
        raise ValueError("incentive.win_margin must be in (0, 1)")
    if not (0.0 < spec.similarity_threshold <= 1.0):
        raise ValueError("preeval.similarity_threshold must be in (0, 1]")
    if spec.facet_weights:
        total = sum(spec.facet_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"eval.facet_weights must sum to 1.0 (got {total})")
    if not spec.repo_pattern:
        raise ValueError("chain.repo_pattern is required")


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> SubnetSpec:
    with open(path_str, "rb") as fh:
        raw = tomllib.load(fh)
    return _build(raw)


def load_spec(path: str | Path | None = None) -> SubnetSpec:
    """Load and cache the subnet spec. Pass ``path`` to override discovery (tests)."""
    return _load_cached(str(_spec_path(path)))
