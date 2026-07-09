"""Dashboard JSON snapshot builder.

Turns local validator state (reign, recent cycle reports, submission queue, stats)
into the ``dashboard.json`` schema the static frontend consumes. Pure and
deterministic given its inputs; ``updated_at`` is passed in by the caller because the
sandbox forbids wall-clock calls inside library code (and it keeps the builder
testable). Publish the result with :mod:`vocence.gateway.dashboard.publish`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional, Sequence

from vocence.domain.spec import SubnetSpec
from vocence.ranking.koth import ReignMember, weight_bps_for_member_count
from vocence.engine.koth_cycle import Candidate
from vocence.engine.koth_coordinator import CycleReport

SCHEMA_VERSION = 1


def reign_to_json(reign: Sequence[ReignMember]) -> List[Dict[str, Any]]:
    """Serialize the court with each king's even-split display weight."""
    ordered = sorted(reign, key=lambda m: m.slot or 99)
    bps = weight_bps_for_member_count(len(ordered))
    out = []
    for i, m in enumerate(ordered):
        out.append({
            "slot": m.slot or (i + 1),
            "uid": m.uid,
            "hotkey": m.hotkey,
            "model_hash": m.model_hash,
            "repo": m.repo,
            "weight": round(bps[i] / 10000, 6) if bps else 0.0,
        })
    return out


def cycle_report_to_run(report: CycleReport) -> Dict[str, Any]:
    """One recent-duel record for the dashboard's eval-runs feed."""
    run: Dict[str, Any] = {
        "run_id": report.run_id,
        "block": report.block,
        "challenger_uid": report.challenger_uid,
        "challenger_hotkey": report.challenger_hotkey,
        "challenger_repo": report.challenger_repo,
        "king_uid": report.reign_uids[0] if report.reign_uids else None,
        "coronated": report.coronated,
        "note": report.note,
        "weights_uids": report.weights_uids,
    }
    duel = report.duel
    if duel is not None:
        run.update({
            "state": duel.state,
            "challenger_won": duel.challenger_won,
            "composite_challenger": duel.composite_challenger,
            "composite_king": duel.composite_king,
            "win_margin": duel.win_margin,
            "scored_samples": duel.scored_samples,
            "total_samples": duel.total_samples,
            "gate_pass_rate": duel.challenger_gate_pass_rate,
            "facets": {
                name: {"king": fs.king_mean, "challenger": fs.challenger_mean,
                       "challenger_win_rate": fs.challenger_win_rate}
                for name, fs in duel.facets.items()
            },
        })
    return run


def build_run_detail(
    report: CycleReport, corpus: Sequence[Any] = ()
) -> Dict[str, Any]:
    """Full per-run detail (published to data/runs/<run_id>.json) for the detail page.

    Includes the aggregate verdict plus a per-sample table: prompt + traits joined from
    the corpus, and king-vs-challenger scores for each facet. This is the albedo-style
    drill-down — every duel becomes an addressable, inspectable record.
    """
    by_id = {getattr(s, "sample_id", None): s for s in corpus}
    samples: List[Dict[str, Any]] = []
    for rec in report.records:
        src = by_id.get(rec.sample_id)
        samples.append({
            "sample_id": rec.sample_id,
            "target_text": getattr(src, "target_text", "") if src else "",
            "traits": getattr(src, "traits", {}) if src else {},
            "scored": rec.scored,
            "king_intelligible": rec.king_intelligible,
            "challenger_intelligible": rec.challenger_intelligible,
            "facets": {
                "intelligibility": {"king": rec.intelligibility.king, "challenger": rec.intelligibility.challenger},
                "adherence": {"king": rec.adherence.king, "challenger": rec.adherence.challenger},
                "naturalness": {"king": rec.naturalness.king, "challenger": rec.naturalness.challenger},
            },
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "run": cycle_report_to_run(report),
        "samples": samples,
    }


def queue_to_json(candidates: Sequence[Candidate]) -> List[Dict[str, Any]]:
    return [
        {"uid": c.uid, "hotkey": c.hotkey, "repo": c.repo, "digest": c.digest, "block": c.block}
        for c in sorted(candidates, key=lambda c: (c.block, c.uid))
    ]


def build_leaderboard(
    reign: Sequence[ReignMember], runs: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Rank participants: current kings first (by slot), then challengers by best composite.

    Aggregates each challenger's history from the runs feed (best composite achieved,
    duels attempted, coronations, last block). Built from ``runs`` (serialized) so it
    survives restarts via the persisted report store.
    """
    king_by_uid = {m.uid: m for m in reign}
    bps = weight_bps_for_member_count(len(reign))
    weight_by_uid = {m.uid: (bps[i] / 10000 if bps else 0.0)
                     for i, m in enumerate(sorted(reign, key=lambda x: x.slot or 99))}

    agg: Dict[int, Dict[str, Any]] = {}
    for run in runs:
        uid = run.get("challenger_uid")
        if uid is None:
            continue
        e = agg.setdefault(uid, {"uid": uid, "hotkey": run.get("challenger_hotkey", ""),
                                 "repo": run.get("challenger_repo", ""), "best_composite": None,
                                 "duels": 0, "coronations": 0, "last_block": 0})
        e["duels"] += 1
        if run.get("coronated"):
            e["coronations"] += 1
        comp = run.get("composite_challenger")
        if comp is not None and (e["best_composite"] is None or comp > e["best_composite"]):
            e["best_composite"] = comp
        e["last_block"] = max(e["last_block"], run.get("block", 0) or 0)
        if run.get("challenger_hotkey"):
            e["hotkey"] = run["challenger_hotkey"]

    board: List[Dict[str, Any]] = []
    for m in sorted(reign, key=lambda x: x.slot or 99):
        hist = agg.get(m.uid, {})
        board.append({
            "uid": m.uid, "hotkey": m.hotkey, "repo": m.repo,
            "status": "king", "slot": m.slot, "weight": round(weight_by_uid.get(m.uid, 0.0), 6),
            "best_composite": hist.get("best_composite"),
            "coronations": hist.get("coronations", 0), "duels": hist.get("duels", 0),
        })
    challengers = [e for uid, e in agg.items() if uid not in king_by_uid]
    challengers.sort(key=lambda e: (e["best_composite"] is None, -(e["best_composite"] or 0)))
    for e in challengers:
        board.append({
            "uid": e["uid"], "hotkey": e["hotkey"], "repo": e["repo"],
            "status": "challenger", "slot": None, "weight": 0.0,
            "best_composite": e["best_composite"], "coronations": e["coronations"], "duels": e["duels"],
        })
    for rank, entry in enumerate(board, 1):
        entry["rank"] = rank
    return board


def build_dashboard(
    *,
    spec: SubnetSpec,
    block: int,
    reign: Sequence[ReignMember],
    reports: Sequence[CycleReport] = (),
    runs: Optional[Sequence[Dict[str, Any]]] = None,
    queue: Sequence[Candidate] = (),
    stats: Optional[Dict[str, Any]] = None,
    updated_at: str = "",
    max_runs: int = 100,
) -> Dict[str, Any]:
    # Prefer pre-serialized runs (from the persistent store); else convert reports.
    if runs is None:
        runs = [cycle_report_to_run(r) for r in reports if r.duel is not None]
    runs = list(runs)[-max_runs:]
    coronations = sum(1 for r in runs if r.get("coronated"))
    leaderboard = build_leaderboard(reign, runs)
    computed_stats = {
        "eval_runs": len(runs),
        "coronations": coronations,
        "court_size": len(reign),
        "queue_depth": len(queue),
        "participants": len(leaderboard),
    }
    if stats:
        computed_stats.update(stats)

    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "spec": {
            "name": spec.name,
            "netuid": spec.netuid,
            "win_margin": spec.win_margin,
            "court_size": spec.court_size,
            "judges": spec.judges,
        },
        "chain": {"block": block},
        "reign": reign_to_json(reign),
        "leaderboard": leaderboard,
        "current_eval": None,
        "queue": queue_to_json(queue),
        "eval_runs": list(reversed(runs)),  # newest first
        "stats": computed_stats,
        "fails": [],
    }
