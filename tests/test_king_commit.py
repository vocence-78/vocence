"""Tests for the explicit king commitment + local recovery store."""

import pytest

from vocence.domain.spec import load_spec
from vocence.ranking.koth import ReignMember
from vocence.engine.king_commit import (
    KingRef, format_king_commitment, parse_king_commitment, KingStateStore, king_court_weights,
)

SPEC = load_spec()
DIG = "sha256:" + "a" * 64


def test_king_commitment_roundtrip():
    king = KingRef(uid=42, hotkey="5Abc", repo="ns/vocence-prompttts-v1", digest=DIG, block=900)
    s = format_king_commitment(king)
    assert s == f"king1|42|5Abc|ns/vocence-prompttts-v1|{DIG}|900"
    parsed = parse_king_commitment(s)
    assert parsed == {"uid": 42, "hotkey": "5Abc", "repo": "ns/vocence-prompttts-v1", "digest": DIG, "block": 900}


@pytest.mark.parametrize("bad", ["", "nope", "king1|x|hk|repo|baddigest|1", "king0|1|hk|r|" + DIG + "|1"])
def test_parse_king_commitment_rejects(bad):
    assert parse_king_commitment(bad) == {}


def test_format_rejects_pipe_in_repo():
    with pytest.raises(ValueError):
        format_king_commitment(KingRef(uid=1, hotkey="hk", repo="ns|evil", digest=DIG))


def test_king_court_weights_matches_court():
    reign = [ReignMember(uid=i, hotkey=f"h{i}", model_hash=f"m{i}", slot=i) for i in (1, 2)]
    uids, weights = king_court_weights(reign, SPEC)
    assert uids == [1, 2]
    assert all(abs(w - 0.5) < 1e-9 for w in weights)
    # empty court burns
    uids, weights = king_court_weights([], SPEC)
    assert uids == [SPEC.burn_uid] and weights == [1.0]


def test_king_state_store_recovery_and_history(tmp_path):
    store = KingStateStore(tmp_path / "king.json")
    assert store.load_current() is None

    k1 = KingRef(uid=1, hotkey="h1", repo="ns/vocence-prompttts-a", digest="sha256:" + "1" * 64, block=100)
    store.save(k1)
    assert store.load_current() == k1

    # re-saving the same king does not grow history
    store.save(k1)
    assert len(store.history()) == 1

    k2 = KingRef(uid=2, hotkey="h2", repo="ns/vocence-prompttts-b", digest="sha256:" + "2" * 64, block=200)
    store.save(k2)
    assert store.load_current() == k2
    assert len(store.history()) == 2

    # a fresh store reads the persisted current king (crash recovery)
    assert KingStateStore(tmp_path / "king.json").load_current() == k2
