"""Tests for vocence.gateway.cli."""

import pytest
from click.testing import CliRunner

from vocence.gateway.cli.main import cli


runner = CliRunner()


class TestCLIInvocation:
    """CLI group and commands load without error."""

    def test_cli_help(self):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "vocence" in result.output.lower()

    def test_serve_help(self):
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0

    def test_api_help(self):
        result = runner.invoke(cli, ["api", "--help"])
        assert result.exit_code == 0

    def test_services_help(self):
        result = runner.invoke(cli, ["services", "--help"])
        assert result.exit_code == 0

    def test_get_miners_help(self):
        result = runner.invoke(cli, ["get-miners", "--help"])
        assert result.exit_code == 0

    def test_owner_help(self):
        result = runner.invoke(cli, ["owner", "--help"])
        assert result.exit_code == 0

    def test_owner_serve_help(self):
        result = runner.invoke(cli, ["owner", "serve", "--help"])
        assert result.exit_code == 0
        assert "rounds" in result.output.lower()

    def test_corpus_help(self):
        result = runner.invoke(cli, ["corpus", "--help"])
        assert result.exit_code == 0

    def test_corpus_source_downloader_help(self):
        result = runner.invoke(cli, ["corpus", "source-downloader", "--help"])
        assert result.exit_code == 0
        assert "rounds" in result.output.lower()

    def test_miner_help(self):
        result = runner.invoke(cli, ["miner", "--help"])
        assert result.exit_code == 0

    def test_miner_push_help(self):
        result = runner.invoke(cli, ["miner", "push", "--help"])
        assert result.exit_code == 0
        assert "model-name" in result.output or "model_name" in result.output.lower()

    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1" in result.output or "version" in result.output.lower()
