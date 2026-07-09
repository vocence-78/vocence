"""Decentralized dashboard: build a JSON snapshot from local validator state and
publish it to Hippius. The static frontend in ``dashboard/`` reads that JSON — there
is no dashboard API server, so the dashboard adds no centralized dependency."""

from vocence.gateway.dashboard.model import build_dashboard, cycle_report_to_run, build_leaderboard
from vocence.gateway.dashboard.store import ReportStore

__all__ = ["build_dashboard", "cycle_report_to_run", "build_leaderboard", "ReportStore"]
