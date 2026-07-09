"""Tests for the leaderboard and persistent report store."""

from vocence.domain.spec import load_spec
from vocence.ranking.koth import ReignMember
from vocence.pipeline.dense_scoring import DuelResult, FacetSummary
from vocence.engine.koth_coordinator import CycleReport
from vocence.gateway.dashboard.model import build_leaderboard, build_dashboard
from vocence.gateway.dashboard.store import ReportStore

SPEC = load_spec()


def _reign(n=2):
    return [ReignMember(uid=i, hotkey=f"hk{i}", model_hash=f"m{i}", slot=i, repo=f"ns/r{i}")
            for i in range(1, n + 1)]


def _run(uid, hk, comp, block, coronated=False):
    return {"block": block, "challenger_uid": uid, "challenger_hotkey": hk,
            "challenger_repo": f"ns/{hk}", "coronated": coronated,
            "composite_challenger": comp, "composite_king": 0.7}


def test_leaderboard_kings_first_then_by_composite():
    reign = _reign(2)  # uids 1,2 are kings
    runs = [
        _run(1, "hk1", 0.80, 100, coronated=True),   # current king, also has history
        _run(9, "hk9", 0.78, 110),                    # challenger
        _run(7, "hk7", 0.83, 120),                    # challenger, higher composite
        _run(9, "hk9", 0.75, 130),                    # 9 again (older/worse)
    ]
    board = build_leaderboard(reign, runs)
    # kings first (slot order), then challengers by best composite desc
    assert [e["uid"] for e in board] == [1, 2, 7, 9]
    assert board[0]["status"] == "king" and board[0]["slot"] == 1
    assert board[2]["status"] == "challenger" and board[2]["best_composite"] == 0.83
    # uid 9 aggregated: 2 duels, best 0.78
    nine = next(e for e in board if e["uid"] == 9)
    assert nine["duels"] == 2 and nine["best_composite"] == 0.78
    assert [e["rank"] for e in board] == [1, 2, 3, 4]


def test_build_dashboard_includes_leaderboard():
    d = build_dashboard(spec=SPEC, block=500, reign=_reign(1),
                        runs=[_run(9, "hk9", 0.9, 100)], updated_at="t")
    assert "leaderboard" in d
    assert d["stats"]["participants"] == len(d["leaderboard"])


def _report(coronated):
    duel = DuelResult(state="succeeded", composite_king=0.72, composite_challenger=0.77,
                      challenger_won=coronated, win_margin=0.03,
                      facets={"intelligibility": FacetSummary(0.9, 0.9, 0.5),
                              "adherence": FacetSummary(0.6, 0.8, 0.7),
                              "naturalness": FacetSummary(0.7, 0.75, 0.6)})
    return CycleReport(block=100, reign_uids=[1], challenger_uid=9, coronated=coronated,
                       weights_uids=[9, 1], weights=[0.5, 0.5], duel=duel, challenger_hotkey="hk9")


def test_report_store_roundtrip(tmp_path):
    store = ReportStore(tmp_path / "reports.jsonl")
    store.append(_report(True))
    store.append(_report(False))
    # a no-duel report is not added to the history feed
    store.append(CycleReport(block=101, reign_uids=[1], challenger_uid=None, coronated=False,
                             weights_uids=[1], weights=[1.0], note="no_challenger"))
    recent = store.recent()
    assert len(recent) == 2
    assert recent[0]["challenger_hotkey"] == "hk9"
    assert recent[0]["coronated"] is True


def test_report_store_recent_limit(tmp_path):
    store = ReportStore(tmp_path / "r.jsonl")
    for _ in range(5):
        store.append(_report(False))
    assert len(store.recent(3)) == 3


def test_build_run_detail_joins_corpus():
    from vocence.pipeline.dense_scoring import FacetPair, SampleRecord
    from vocence.pipeline.duel import CorpusSample
    from vocence.gateway.dashboard.model import build_run_detail

    recs = [
        SampleRecord("s0", FacetPair(0.90, 0.95), FacetPair(0.60, 0.80), FacetPair(0.70, 0.72), True, True),
        SampleRecord("s1", FacetPair(0.93, 0.55), FacetPair(0.62, 0.70), FacetPair(0.71, 0.66), True, False),
    ]
    corpus = [CorpusSample("s0", "hello world", {"gender": "female"}),
              CorpusSample("s1", "another line", {"emotion": "calm"})]
    report = _report(True)
    report.run_id = "100-9"
    report.records = recs

    detail = build_run_detail(report, corpus)
    assert detail["run"]["run_id"] == "100-9"
    assert len(detail["samples"]) == 2
    s0 = detail["samples"][0]
    assert s0["target_text"] == "hello world"
    assert s0["traits"]["gender"] == "female"
    assert s0["facets"]["adherence"]["challenger"] == 0.80
    assert detail["samples"][1]["challenger_intelligible"] is False
