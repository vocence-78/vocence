"""Tests for pure chain-state derivation (reveals -> candidates + reign)."""

from vocence.domain.spec import load_spec
from vocence.adapters.chain import format_reveal
from vocence.engine.chain_gateway import (
    latest_reveals, candidates_from_reveals, reign_from_reveals,
)

SPEC = load_spec()
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def _raw():
    return {
        0: ("hk0", format_reveal("ns/vocence-prompttts-a", D1), 100),
        1: ("hk1", format_reveal("ns/vocence-prompttts-b", D2), 120),
        2: ("hk2", '{"model_name":"legacy"}', 130),   # legacy JSON -> ignored
        3: ("hk3", "", 140),                            # empty -> ignored
    }


def test_latest_reveals_keeps_only_v7():
    reveals = latest_reveals(_raw())
    assert set(reveals) == {0, 1}
    assert reveals[0].repo == "ns/vocence-prompttts-a"
    assert reveals[0].digest == D1
    assert reveals[0].model_hash == D1  # digest is the identity


def test_candidates_from_reveals():
    cands = candidates_from_reveals(latest_reveals(_raw()))
    uids = sorted(c.uid for c in cands)
    assert uids == [0, 1]
    c0 = next(c for c in cands if c.uid == 0)
    assert c0.repo == "ns/vocence-prompttts-a" and c0.block == 100
    assert c0.submission_id.startswith("0:")


def test_reign_from_reveals_orders_by_incentive():
    reveals = latest_reveals(_raw())
    reign = reign_from_reveals(reveals, {0: 0.2, 1: 0.8}, SPEC)
    assert [m.uid for m in reign] == [1, 0]   # uid1 higher incentive -> slot 1
    assert reign[0].repo == "ns/vocence-prompttts-b"
    assert reign[0].digest == D2
