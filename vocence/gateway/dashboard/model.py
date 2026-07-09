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
        "block": report.block,
        "challenger_uid": report.challenger_uid,
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


def queue_to_json(candidates: Sequence[Candidate]) -> List[Dict[str, Any]]:
    return [
        {"uid": c.uid, "hotkey": c.hotkey, "repo": c.repo, "digest": c.digest, "block": c.block}
        for c in sorted(candidates, key=lambda c: (c.block, c.uid))
    ]


def build_dashboard(
    *,
    spec: SubnetSpec,
    block: int,
    reign: Sequence[ReignMember],
    reports: Sequence[CycleReport],
    queue: Sequence[Candidate] = (),
    stats: Optional[Dict[str, Any]] = None,
    updated_at: str = "",
    max_runs: int = 100,
) -> Dict[str, Any]:
    runs = [cycle_report_to_run(r) for r in reports if r.duel is not None][-max_runs:]
    coronations = sum(1 for r in reports if r.coronated)
    computed_stats = {
        "eval_runs": len([r for r in reports if r.duel is not None]),
        "coronations": coronations,
        "court_size": len(reign),
        "queue_depth": len(queue),
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
        "current_eval": None,
        "queue": queue_to_json(queue),
        "eval_runs": list(reversed(runs)),  # newest first
        "stats": computed_stats,
        "fails": [],
    }
