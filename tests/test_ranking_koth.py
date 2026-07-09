"""Tests for the top-5 KOTH court logic."""

import pytest

from vocence.ranking.koth import (
    ReignMember,
    build_reign_plan,
    challenger_beats_king,
    weight_bps_for_member_count,
    weight_epoch_payload,
    retired_members,
    BPS_TOTAL,
)


def _m(uid, slot=0, hk=None, mh=None):
    return ReignMember(uid=uid, hotkey=hk or f"hk{uid}", model_hash=mh or f"mh{uid}", slot=slot)


def test_win_margin():
    assert challenger_beats_king(0.75, 0.70, win_margin=0.03) is True
    assert challenger_beats_king(0.7365, 0.7262, win_margin=0.03) is False  # the live-run case
    assert challenger_beats_king(0.70, 0.75, win_margin=0.03) is False


@pytest.mark.parametrize(
    "count,expected",
    [(0, []), (1, [10000]), (2, [5000, 5000]), (4, [2500, 2500, 2500, 2500]),
     (5, [2000, 2000, 2000, 2000, 2000]), (3, [3334, 3333, 3333])],
)
def test_weight_bps_split(count, expected):
    bps = weight_bps_for_member_count(count)
    assert bps == expected
    assert sum(bps) == (BPS_TOTAL if count else 0)


def test_coronation_into_empty_reign():
    plan = build_reign_plan([], _m(10))
    assert len(plan) == 1
    assert plan[0].slot == 1 and plan[0].is_challenger
    assert plan[0].weight_bps == 10000


def test_coronation_shifts_and_caps_at_five():
    active = [_m(1, 1), _m(2, 2), _m(3, 3), _m(4, 4), _m(5, 5)]
    plan = build_reign_plan(active, _m(99), court_size=5)
    assert [p.member.uid for p in plan] == [99, 1, 2, 3, 4]  # challenger slot1, uid5 retired
    assert [p.slot for p in plan] == [1, 2, 3, 4, 5]
    assert all(p.weight_bps == 2000 for p in plan)
    retired = retired_members(active, plan)
    assert [m.uid for m in retired] == [5]


def test_challenger_dedup_removes_same_hotkey_or_model():
    active = [_m(1, 1, hk="alice", mh="A"), _m(2, 2, hk="bob", mh="B")]
    # challenger shares hotkey with uid1 → uid1 must not also appear
    plan = build_reign_plan(active, _m(1, hk="alice", mh="A2"))
    uids = [p.member.uid for p in plan]
    assert uids == [1, 2]  # challenger(uid1) slot1, bob slot2, old alice-entry dropped
    assert plan[0].is_challenger


def test_weight_epoch_payload_normalizes():
    active = [_m(1, 1), _m(2, 2)]
    plan = build_reign_plan(active, _m(3))
    uids, weights = weight_epoch_payload(plan)
    assert uids == [3, 1, 2]
    assert pytest.approx(sum(weights), abs=1e-9) == 1.0
    # even split with remainder to slot 1: 3334 / 3333 / 3333 bps
    assert weights == [0.3334, 0.3333, 0.3333]


def test_empty_plan_burns():
    uids, weights = weight_epoch_payload([], burn_uid=0)
    assert uids == [0] and weights == [1.0]
