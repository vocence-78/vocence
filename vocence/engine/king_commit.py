"""Explicit king reference: on-chain commitment + local recovery state.

The vtrust-critical rule is that weights are a deterministic function of the *shared*
king court, never of a validator's raw verdict. The court is derived from on-chain
incentive ([[reign]]), but incentive lags a coronation by a cycle, so we also persist
the king **explicitly**: a validator commits the reigning king reference on-chain
(``set_commitment``) and mirrors it to a local file. That gives an unambiguous,
crash-recoverable source of truth for "who is king right now" (teutonic's model).

Serialize/parse and the local store are pure and unit-tested; the on-chain write lives
in :func:`vocence.adapters.deployment.commit_king_command`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from vocence.domain.spec import SubnetSpec
from vocence.ranking.koth import ReignMember
from vocence.engine.koth_cycle import current_reign_weights

KING_COMMIT_VERSION = "king1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class KingRef:
    uid: int
    hotkey: str
    repo: str
    digest: str
    block: int = 0


def format_king_commitment(king: KingRef) -> str:
    """``king1|uid|hotkey|repo|digest|block`` for on-chain set_commitment."""
    if "|" in king.repo or "|" in king.hotkey:
        raise ValueError("repo/hotkey must not contain '|'")
    digest = king.digest.strip().lower()
    if not _DIGEST_RE.match(digest):
        raise ValueError(f"invalid king digest: {king.digest!r}")
    return f"{KING_COMMIT_VERSION}|{king.uid}|{king.hotkey}|{king.repo}|{digest}|{king.block}"


def parse_king_commitment(value: str) -> Dict[str, Any]:
    """Parse a king commitment; ``{}`` on any malformed/non-king value (never raises)."""
    if not value or not isinstance(value, str):
        return {}
    parts = value.strip().split("|")
    if len(parts) != 6 or parts[0] != KING_COMMIT_VERSION:
        return {}
    _, uid, hotkey, repo, digest, block = parts
    digest = digest.strip().lower()
    if not repo or not hotkey or not _DIGEST_RE.match(digest):
        return {}
    try:
        return {"uid": int(uid), "hotkey": hotkey, "repo": repo, "digest": digest, "block": int(block)}
    except ValueError:
        return {}


def king_court_weights(reign, spec: SubnetSpec):
    """The single source of weights: even-split over the shared king court (or burn).

    Weights are ALWAYS derived from the court here — never from a private eval verdict —
    which is what keeps every honest validator's weight vector identical (high vtrust).
    """
    return current_reign_weights(reign, spec)


class KingStateStore:
    """Local mirror of the reigning king for crash recovery + an append-only history."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"current": None, "history": []}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {"current": None, "history": []}

    def load_current(self) -> Optional[KingRef]:
        cur = self._read().get("current")
        return KingRef(**cur) if cur else None

    def history(self) -> List[Dict[str, Any]]:
        return self._read().get("history", [])

    def save(self, king: KingRef) -> None:
        """Set the current king; append to history only when the digest actually changes."""
        state = self._read()
        cur = state.get("current")
        if not cur or cur.get("digest") != king.digest:
            state.setdefault("history", []).append(asdict(king))
        state["current"] = asdict(king)
        self.path.write_text(json.dumps(state, indent=2))
