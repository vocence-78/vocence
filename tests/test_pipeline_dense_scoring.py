"""Tests for WER and the dense pairwise duel aggregation."""

import pytest

from vocence.domain.spec import load_spec
from vocence.pipeline.wer import word_error_rate, character_error_rate, intelligibility_score, normalize_text
from vocence.pipeline.dense_scoring import (
    FacetPair, SampleRecord, aggregate_duel, FACETS,
)

SPEC = load_spec()


# ------------------------------------------------------------------ WER
def test_wer_exact_match():
    assert word_error_rate("Hello, world!", "hello world") == 0.0


def test_wer_normalization():
    assert normalize_text("Héllo,  WORLD!!") == "héllo world"


def test_wer_one_substitution():
    assert word_error_rate("the cat sat", "the dog sat") == pytest.approx(1 / 3)


def test_wer_empty_ref():
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "extra words") == 1.0


def test_cer_and_score():
    assert character_error_rate("abc", "abc") == 0.0
    assert intelligibility_score(0.0) == 1.0
    assert intelligibility_score(0.15) == pytest.approx(0.85)
    assert intelligibility_score(2.0) == 0.0


# ------------------------------------------------------------------ duel
def _rec(sid, ii, ai, ni, king_ok=True, chal_ok=True, scored=True):
    """ii/ai/ni are (king, challenger) tuples for the three facets."""
    return SampleRecord(
        sample_id=sid,
        intelligibility=FacetPair(*ii),
        adherence=FacetPair(*ai),
        naturalness=FacetPair(*ni),
        king_intelligible=king_ok,
        challenger_intelligible=chal_ok,
        scored=scored,
    )


def test_duel_challenger_wins_by_margin():
    recs = [_rec(f"s{i}", (0.9, 0.92), (0.6, 0.8), (0.5, 0.7)) for i in range(10)]
    res = aggregate_duel(recs, SPEC)
    assert res.state == "succeeded"
    assert res.challenger_won is True
    assert res.composite_challenger > res.composite_king
    assert set(res.facets) == set(FACETS)


def test_duel_below_margin_does_not_crown():
    # tiny lead under the 3% margin (the live-run 0.7365 vs 0.7262 situation)
    recs = [_rec(f"s{i}", (0.9, 0.9), (0.72, 0.73), (0.73, 0.735)) for i in range(20)]
    res = aggregate_duel(recs, SPEC)
    assert res.state == "succeeded"
    assert (res.composite_challenger - res.composite_king) < SPEC.win_margin
    assert res.challenger_won is False


def test_duel_intelligibility_gate_disqualifies():
    # challenger dominates adherence+naturalness but fails the intelligibility gate
    recs = [_rec(f"s{i}", (0.9, 0.1), (0.5, 0.99), (0.5, 0.99), chal_ok=False) for i in range(10)]
    res = aggregate_duel(recs, SPEC)
    assert res.challenger_won is False
    assert res.reason == "intelligibility_gate_failed"
    assert res.challenger_gate_pass_rate == 0.0


def test_duel_fails_when_too_few_scored():
    recs = [_rec(f"s{i}", (0.9, 0.9), (0.5, 0.5), (0.5, 0.5), scored=(i < 3)) for i in range(10)]
    res = aggregate_duel(recs, SPEC)
    assert res.state == "failed"
    assert res.challenger_won is None


def test_duel_win_rate_counts_ties():
    recs = [
        _rec("a", (0.9, 0.9), (0.5, 0.6), (0.5, 0.5)),  # adherence chal wins, nat tie
        _rec("b", (0.9, 0.9), (0.5, 0.4), (0.5, 0.5)),  # adherence king wins, nat tie
    ]
    res = aggregate_duel(recs, SPEC)
    assert res.facets["naturalness"].challenger_win_rate == 0.5
    assert res.facets["adherence"].challenger_win_rate == 0.5
