"""
Logging utilities for Vocence validator.

Provides formatted, timestamped log messages with color-coded prefixes.
Also writes to a daily rotating log file (UTC, one .log file per day) when LOG_DIR is set.
"""

import os
from datetime import datetime, timezone

from rich import print as rprint
from rich.console import Console
from rich.table import Table


_console = Console()


def _daily_log_path() -> str | None:
    """Path for today's log file (UTC date). Returns None if LOG_DIR is disabled."""
    try:
        from vocence.domain.config import LOG_DIR
    except Exception:
        return None
    if not (LOG_DIR and str(LOG_DIR).strip()):
        return None
    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR.strip(), f"vocence_{date_str}.log")


def _write_to_daily_log(line: str) -> None:
    """Append one line to the daily log file (UTC). No-op if LOG_DIR is empty or write fails."""
    path = _daily_log_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")
    except Exception:
        pass


def print_banner() -> None:
    """Print the Vocence ASCII art banner (e.g. on CLI start). Uses Rich for bold cyan/white."""
    rprint("""
[bold cyan]
██╗   ██╗ ██████╗  ██████╗███████╗███╗   ██╗ ██████╗███████╗
██║   ██║██╔═══██╗██╔════╝██╔════╝████╗  ██║██╔════╝██╔════╝
██║   ██║██║   ██║██║     █████╗  ██╔██╗ ██║██║     █████╗
╚██╗ ██╔╝██║   ██║██║     ██╔══╝  ██║╚██╗██║██║     ██╔══╝
 ╚████╔╝ ╚██████╔╝╚██████╗███████╗██║ ╚████║╚██████╗███████╗
  ╚═══╝   ╚═════╝  ╚═════╝╚══════╝╚═╝  ╚═══╝ ╚═════╝╚══════╝
[/bold cyan]

[bold white]real-time ai voice engine[/bold white]
""")


def emit_log(message: str, severity: str = "info") -> None:
    """Format and print timestamped log messages with color-coded prefixes.
    Also appends to the daily log file (UTC) when LOG_DIR is set.

    Args:
        message: The message to log
        severity: Log severity - one of "info", "success", "error", "warn", "start"
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    severity_prefixes = {
        "info": f"\033[90m{timestamp}\033[0m \033[36m▸\033[0m",
        "success": f"\033[90m{timestamp}\033[0m \033[32m✓\033[0m",
        "error": f"\033[90m{timestamp}\033[0m \033[31m✗\033[0m",
        "warn": f"\033[90m{timestamp}\033[0m \033[33m⚠\033[0m",
        "start": f"\033[90m{timestamp}\033[0m \033[33m→\033[0m",
    }
    print(f"{severity_prefixes.get(severity, f'\033[90m{timestamp}\033[0m  ')} {message}")
    ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_to_daily_log(f"{ts_utc} [{severity.upper()}] {message}")


def print_header(header_text: str) -> None:
    """Print a bold section header.
    Also appends to the daily log file (UTC) when LOG_DIR is set.

    Args:
        header_text: The header text to display
    """
    print(f"\n\033[1m{'─' * 60}\033[0m\n\033[1m{header_text}\033[0m\n\033[1m{'─' * 60}\033[0m\n")
    ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_to_daily_log(f"{ts_utc} --- {header_text} ---")


def print_table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    """Print a Rich table and append a plain-text version to the daily log.

    Args:
        title: Table title shown in terminal/log file
        columns: Column headers
        rows: Table rows as strings
    """
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*[str(cell) for cell in row])

    _console.print(table)

    ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_to_daily_log(f"{ts_utc} --- {title} ---")
    header = " | ".join(columns)
    _write_to_daily_log(header)
    _write_to_daily_log("-" * len(header))
    for row in rows:
        _write_to_daily_log(" | ".join(str(cell).replace("\n", " // ") for cell in row))
