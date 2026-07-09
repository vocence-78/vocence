"""Tests for the dashboard JSON snapshot builder."""

from vocence.domain.spec import load_spec
from vocence.ranking.koth import ReignMember
from vocence.pipeline.dense_scoring import DuelResult, FacetSummary
from vocence.engine.koth_cycle import Candidate
from vocence.engine.koth_coordinator import CycleReport
from vocence.gateway.dashboard.model import build_dashboard, cycle_report_to_run, reign_to_json

SPEC = load_spec()


def _reign(n=3):
    return [ReignMember(uid=i, hotkey=f"hk{i}", model_hash=f"m{i}", slot=i, repo=f"ns/r{i}")
            for i in range(1, n + 1)]


def _report(coronated=True):
    duel = DuelResult(
        state="succeeded", composite_king=0.72, composite_challenger=0.76,
        challenger_won=coronated, win_margin=0.03, scored_samples=120, total_samples=128,
        challenger_gate_pass_rate=0.98,
        facets={"intelligibility": FacetSummary(0.9, 0.9, 0.5),
                "adherence": FacetSummary(0.6, 0.8, 0.7),
                "naturalness": FacetSummary(0.7, 0.75, 0.65)},
    )
    return CycleReport(block=100, reign_uids=[1, 2, 3], challenger_uid=9,
                       coronated=coronated, weights_uids=[9, 1, 2, 3], weights=[0.25]*4, duel=duel)


def test_reign_to_json_even_split():
    js = reign_to_json(_reign(4))
    assert [m["slot"] for m in js] == [1, 2, 3, 4]
    assert all(abs(m["weight"] - 0.25) < 1e-9 for m in js)


def test_cycle_report_to_run_has_facets():
    run = cycle_report_to_run(_report())
    assert run["challenger_won"] is True
    assert run["challenger_uid"] == 9
    assert set(run["facets"]) == {"intelligibility", "adherence", "naturalness"}
    assert run["facets"]["adherence"]["challenger"] == 0.8


def test_build_dashboard_schema():
    d = build_dashboard(
        spec=SPEC, block=999, reign=_reign(3),
        reports=[_report(True), _report(False)],
        queue=[Candidate(uid=9, hotkey="n", repo="ns/vocence-prompttts-v1", digest="d", model_hash="m", block=50)],
        updated_at="2026-07-09T00:00:00Z",
    )
    assert d["schema_version"] == 1
    assert d["spec"]["name"] == "Vocence"
    assert d["chain"]["block"] == 999
    assert len(d["reign"]) == 3
    assert d["stats"]["coronations"] == 1
    assert d["stats"]["eval_runs"] == 2
    assert len(d["queue"]) == 1
    # newest run first
    assert d["eval_runs"][0]["block"] == 100
    assert d["updated_at"] == "2026-07-09T00:00:00Z"
