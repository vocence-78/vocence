"""Tests for the KOTH cycle coronation decision."""

from vocence.domain.spec import load_spec
from vocence.ranking.koth import ReignMember
from vocence.pipeline.dense_scoring import DuelResult
from vocence.engine.koth_cycle import (
    Candidate, lead_king, current_reign_weights, plan_after_duel,
    select_challenger, challenger_from_candidate,
)

SPEC = load_spec()


def _reign(n):
    return [ReignMember(uid=i, hotkey=f"hk{i}", model_hash=f"mh{i}", slot=i) for i in range(1, n + 1)]


def _duel(won, state="succeeded"):
    return DuelResult(state=state, composite_king=0.7, composite_challenger=0.75 if won else 0.71,
                      challenger_won=won, win_margin=0.03)


def test_lead_king():
    assert lead_king(_reign(3)).uid == 1
    assert lead_king([]) is None


def test_current_reign_weights_even_split():
    uids, weights = current_reign_weights(_reign(4), SPEC)
    assert uids == [1, 2, 3, 4]
    assert all(abs(w - 0.25) < 1e-9 for w in weights)


def test_empty_reign_burns():
    uids, weights = current_reign_weights([], SPEC)
    assert uids == [SPEC.burn_uid] and weights == [1.0]


def test_coronation_promotes_challenger():
    reign = _reign(5)
    chal = challenger_from_candidate(Candidate(uid=99, hotkey="new", repo="r", digest="d", model_hash="mNEW"))
    uids, weights, coronated = plan_after_duel(reign, chal, _duel(True), SPEC)
    assert coronated is True
    assert uids[0] == 99            # challenger takes slot 1
    assert 5 not in uids           # 5th king retired (court capped at 5)
    assert abs(sum(weights) - 1.0) < 1e-9


def test_no_coronation_keeps_reign():
    reign = _reign(3)
    chal = challenger_from_candidate(Candidate(uid=99, hotkey="new", repo="r", digest="d", model_hash="mNEW"))
    uids, weights, coronated = plan_after_duel(reign, chal, _duel(False), SPEC)
    assert coronated is False
    assert uids == [1, 2, 3]


def test_failed_duel_keeps_reign():
    reign = _reign(2)
    chal = challenger_from_candidate(Candidate(uid=99, hotkey="n", repo="r", digest="d", model_hash="m"))
    _, _, coronated = plan_after_duel(reign, chal, _duel(False, state="failed"), SPEC)
    assert coronated is False


def test_select_challenger_earliest_block():
    cands = [
        Candidate(uid=5, hotkey="e", repo="r", digest="d", model_hash="m5", block=200),
        Candidate(uid=3, hotkey="c", repo="r", digest="d", model_hash="m3", block=100),
    ]
    assert select_challenger(cands, _reign(1)).uid == 3


def test_select_challenger_skips_current_king():
    king_reign = [ReignMember(uid=1, hotkey="king", model_hash="mk", slot=1)]
    cands = [Candidate(uid=1, hotkey="king", repo="r", digest="d", model_hash="mk", block=1)]
    assert select_challenger(cands, king_reign) is None
