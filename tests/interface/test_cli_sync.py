"""Tests for the sync CLI command."""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()


def strip_ansi(text):
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def test_sync_command_help():
    """Test sync command help text."""
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    output = strip_ansi(result.stdout)
    assert "Sync your Obsidian notes to Anki" in output
    assert "--prune" in output
    assert "--backend" in output


@patch("arete.application.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_sync_command_basic(mock_resolve_config, mock_run_sync):
    """Test basic sync command execution."""
    mock_config = MagicMock()
    mock_resolve_config.return_value = mock_config
    mock_run_sync.return_value = None

    result = runner.invoke(app, ["sync", "/tmp/vault"])

    assert result.exit_code == 0
    mock_resolve_config.assert_called_once()
    mock_run_sync.assert_called_once()

    call_args = mock_resolve_config.call_args[0][0]
    expected_path = str(Path("/tmp/vault"))
    assert str(call_args["root_input"]) == expected_path


@patch("arete.application.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_sync_command_with_flags(mock_resolve_config, mock_run_sync):
    """Test sync command with various flags."""
    mock_config = MagicMock()
    mock_resolve_config.return_value = mock_config
    mock_run_sync.return_value = None

    result = runner.invoke(
        app,
        [
            "sync",
            "/tmp/vault",
            "--prune",
            "--force",
            "--dry-run",
            "--backend",
            "ankiconnect",
            "--workers",
            "4",
        ],
    )

    assert result.exit_code == 0

    call_args = mock_resolve_config.call_args[0][0]
    assert call_args["prune"] is True
    assert call_args["force"] is True
    assert call_args["dry_run"] is True
    assert call_args["backend"] == "ankiconnect"
    assert call_args["workers"] == 4


@patch("arete.application.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_sync_command_verbose_flag(mock_resolve_config, mock_run_sync):
    """Test verbose flag increments verbosity."""
    mock_config = MagicMock()
    mock_resolve_config.return_value = mock_config
    mock_run_sync.return_value = None

    result = runner.invoke(app, ["-v", "sync", "."])
    assert result.exit_code == 0
    call_args = mock_resolve_config.call_args[0][0]
    assert call_args["verbose"] == 1

    result = runner.invoke(app, ["-v", "-v", "sync", "."])
    assert result.exit_code == 0
    call_args = mock_resolve_config.call_args[0][0]
    assert call_args["verbose"] == 2


@patch("arete.application.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_sync_command_no_path_uses_cwd(mock_resolve_config, mock_run_sync):
    """Test sync without path argument defaults to CWD."""
    mock_config = MagicMock()
    mock_resolve_config.return_value = mock_config
    mock_run_sync.return_value = None

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    call_args = mock_resolve_config.call_args[0][0]
    assert "root_input" not in call_args or call_args["root_input"] is None


@patch("arete.application.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_sync_command_anki_connect_url(mock_resolve_config, mock_run_sync):
    """Test custom AnkiConnect URL."""
    mock_config = MagicMock()
    mock_resolve_config.return_value = mock_config
    mock_run_sync.return_value = None

    result = runner.invoke(app, ["sync", ".", "--anki-connect-url", "http://custom:9999"])

    assert result.exit_code == 0
    call_args = mock_resolve_config.call_args[0][0]
    assert call_args["anki_connect_url"] == "http://custom:9999"


@patch("arete.application.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_sync_command_clear_cache(mock_resolve_config, mock_run_sync):
    """Test --clear-cache flag."""
    mock_config = MagicMock()
    mock_resolve_config.return_value = mock_config
    mock_run_sync.return_value = None

    result = runner.invoke(app, ["sync", ".", "--clear-cache"])

    assert result.exit_code == 0
    call_args = mock_resolve_config.call_args[0][0]
    assert call_args["clear_cache"] is True
