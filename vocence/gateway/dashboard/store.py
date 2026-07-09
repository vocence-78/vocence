"""Persistent cycle-report store (JSONL).

Each cycle appends one serialized run record so the dashboard's history — and the
leaderboard aggregated from it — survive validator restarts. Newest records are read
back on startup and fed to :func:`build_dashboard` as ``runs``. Plain append-only
JSONL: durable, human-readable, no database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from vocence.engine.koth_coordinator import CycleReport
from vocence.gateway.dashboard.model import cycle_report_to_run


class ReportStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, report: CycleReport) -> Dict[str, Any]:
        """Persist a report (only those with a duel add to the history feed). Returns the run."""
        run = cycle_report_to_run(report)
        if report.duel is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(run, separators=(",", ":")) + "\n")
        return run

    def append_run(self, run: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(run, separators=(",", ":")) + "\n")

    def recent(self, n: int = 100) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        runs: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip a partially-written trailing line
        return runs[-n:]
