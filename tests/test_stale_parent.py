"""Tests for stale-parent enforcement (reveal carries target king digest)."""

import pytest

from vocence.adapters.chain import format_reveal, parse_reveal
from vocence.engine.koth_cycle import Candidate
from vocence.engine.chain_gateway import latest_reveals, candidates_from_reveals, drop_stale_parents

REPO = "ns/vocence-prompttts-v1"
D = "sha256:" + "a" * 64
KING = "sha256:" + "b" * 64


def test_reveal_roundtrip_with_king_digest():
    reveal = format_reveal(REPO, D, KING)
    assert reveal == f"v7|{REPO}|{D}|{KING}"
    parsed = parse_reveal(reveal)
    assert parsed["digest"] == D and parsed["king_digest"] == KING


def test_reveal_without_king_digest_still_valid():
    parsed = parse_reveal(format_reveal(REPO, D))
    assert parsed["digest"] == D and parsed["king_digest"] == ""


def test_reveal_rejects_bad_king_digest():
    assert parse_reveal(f"v7|{REPO}|{D}|not-a-digest") == {}
    with pytest.raises(ValueError):
        format_reveal(REPO, D, "bad")


def test_latest_reveals_carries_king_digest():
    raw = {0: ("hk0", format_reveal(REPO, D, KING), 100)}
    r = latest_reveals(raw)
    assert r[0].king_digest == KING
    c = candidates_from_reveals(r)[0]
    assert c.parent_king_digest == KING


def test_drop_stale_parents():
    cands = [
        Candidate(uid=1, hotkey="a", repo="r", digest="d1", model_hash="m1", parent_king_digest=KING),      # current
        Candidate(uid=2, hotkey="b", repo="r", digest="d2", model_hash="m2", parent_king_digest="sha256:" + "c" * 64),  # stale
        Candidate(uid=3, hotkey="c", repo="r", digest="d3", model_hash="m3", parent_king_digest=""),          # no parent -> kept
    ]
    kept = drop_stale_parents(cands, KING)
    assert sorted(c.uid for c in kept) == [1, 3]


def test_drop_stale_parents_noop_without_king():
    cands = [Candidate(uid=1, hotkey="a", repo="r", digest="d", model_hash="m", parent_king_digest=KING)]
    assert drop_stale_parents(cands, "") == cands  # genesis: nothing to match
