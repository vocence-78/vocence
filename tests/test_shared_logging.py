"""Tests for vocence.shared.logging."""

import pytest
from unittest.mock import patch

from vocence.shared.logging import emit_log, print_header


class TestEmitLog:
    """Tests for emit_log."""

    def test_emit_log_prints(self, capsys):
        emit_log("test message", "info")
        out, _ = capsys.readouterr()
        assert "test message" in out

    def test_emit_log_severity_info(self, capsys):
        emit_log("info msg", "info")
        out, _ = capsys.readouterr()
        assert "info msg" in out

    def test_emit_log_severity_error(self, capsys):
        emit_log("error msg", "error")
        out, _ = capsys.readouterr()
        assert "error msg" in out


class TestPrintHeader:
    """Tests for print_header."""

    def test_print_header_contains_text(self, capsys):
        print_header("My Section")
        out, _ = capsys.readouterr()
        assert "My Section" in out

    def test_print_header_prints_separator(self, capsys):
        print_header("Title")
        out, _ = capsys.readouterr()
        assert "─" in out or "Title" in out
