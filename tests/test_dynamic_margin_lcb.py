"""Tests for the dynamic margin + bootstrap-LCB coronation rule."""

import dataclasses
import pytest

from vocence.domain.spec import load_spec
from vocence.pipeline.dense_scoring import (
    FacetPair, SampleRecord, aggregate_duel, bootstrap_lcb,
)

SPEC = load_spec()


def _rec(sid, k, c, gate=True):
    """Same score k/c on all three facets (composite delta = c-k)."""
    return SampleRecord(sid, FacetPair(k, c), FacetPair(k, c), FacetPair(k, c), True, gate)


# ------------------------------------------------------------------ dynamic margin
def test_margin_scales_with_king_headroom():
    # low king -> big required margin; near-perfect king -> tiny margin (floored)
    assert SPEC.margin_for(0.0) == pytest.approx(SPEC.margin_coefficient)         # c*(1-0)
    assert SPEC.margin_for(0.5) == pytest.approx(SPEC.margin_coefficient * 0.5)
    assert SPEC.margin_for(0.99) == SPEC.min_margin                               # floored
    assert SPEC.margin_for(1.0) == SPEC.min_margin


def test_effective_margin_recorded():
    recs = [_rec(f"s{i}", 0.80, 0.90) for i in range(20)]
    res = aggregate_duel(recs, SPEC)
    # king composite 0.80 -> margin_t = 0.10*(1-0.80) = 0.02
    assert res.win_margin == pytest.approx(0.02, abs=1e-6)


# ------------------------------------------------------------------ LCB
def test_bootstrap_lcb_deterministic():
    d = [0.05, 0.02, 0.09, -0.01, 0.04, 0.03]
    a = bootstrap_lcb(d, n_boot=1000, alpha=0.05, seed=SPEC.bootstrap_seed)
    b = bootstrap_lcb(d, n_boot=1000, alpha=0.05, seed=SPEC.bootstrap_seed)
    assert a == b                       # fixed seed -> reproducible
    assert a < sum(d) / len(d)          # LCB below the mean


def test_lcb_rejects_noisy_win():
    # positive MEAN advantage but high variance -> LCB below margin -> not crowned
    king, chal = 0.70, None
    deltas = [0.30, -0.28, 0.31, -0.27, 0.29, -0.26, 0.32, -0.25]  # mean ~ +0.02, huge spread
    recs = []
    for i, d in enumerate(deltas):
        recs.append(SampleRecord(f"s{i}", FacetPair(king, king + d),
                                 FacetPair(king, king + d), FacetPair(king, king + d), True, True))
    res = aggregate_duel(recs, SPEC)
    assert res.composite_challenger > res.composite_king   # positive mean
    assert res.lcb < res.win_margin                        # but not confident
    assert res.challenger_won is False
    assert res.reason == "lcb_below_margin"


def test_lcb_accepts_consistent_win():
    # small but CONSISTENT advantage -> tight LCB above margin -> crowned
    recs = [_rec(f"s{i}", 0.70, 0.75) for i in range(40)]  # +0.05 every sample
    res = aggregate_duel(recs, SPEC)
    # margin_t = 0.10*(1-0.70)=0.03; consistent delta 0.05 -> LCB ~0.05 > 0.03
    assert res.lcb == pytest.approx(0.05, abs=1e-6)
    assert res.win_margin == pytest.approx(0.03, abs=1e-6)
    assert res.challenger_won is True
