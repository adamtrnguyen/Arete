"""Tests for the sync CLI command."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


@patch("arete.composition.orchestrator.run_sync_logic")
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


@patch("arete.composition.orchestrator.run_sync_logic")
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


@patch("arete.composition.orchestrator.run_sync_logic")
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


@patch("arete.composition.orchestrator.run_sync_logic")
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


@patch("arete.composition.orchestrator.run_sync_logic")
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


@patch("arete.composition.orchestrator.run_sync_logic")
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


@patch("arete.composition.orchestrator.run_sync_logic")
@patch("arete.interface._common.resolve_config")
def test_only_yes_confirms_a_prune(mock_resolve_config, mock_run_sync):
    """--force re-sends cards; --yes is the one flag that confirms a prune."""
    from arete.interface.cli import _confirm_prune_at_prompt

    mock_resolve_config.return_value = MagicMock()
    mock_run_sync.return_value = None

    runner.invoke(app, ["sync", "/tmp/vault", "--prune", "--force"])
    kwargs = mock_run_sync.call_args.kwargs
    assert kwargs["confirm_prune"] is False
    assert kwargs["prompt"] is _confirm_prune_at_prompt

    runner.invoke(app, ["sync", "/tmp/vault", "--prune", "--yes"])
    assert mock_run_sync.call_args.kwargs["confirm_prune"] is True


# --- sync --json ---


def _json_sync(execute, *args: str, keep_going: bool = False):
    """Run `arete sync --json <args>` with execute_sync replaced; return the result."""
    config = MagicMock(keep_going=keep_going)
    with (
        patch("arete.interface.cli._resolve_with_overrides", return_value=config),
        patch("arete.composition.orchestrator.execute_sync", execute),
    ):
        return runner.invoke(app, ["sync", "/tmp/arete-vault-not-read", "--json", *args])


def test_json_never_prompts_and_passes_yes_through():
    from arete.application.sync.pipeline import RunStats

    execute = AsyncMock(return_value=RunStats(1, 1, 0))
    _json_sync(execute, "--prune")
    assert execute.call_args.kwargs == {"confirm_prune": False, "prompt": None}
    _json_sync(execute, "--prune", "--yes")
    assert execute.call_args.kwargs == {"confirm_prune": True, "prompt": None}


def test_json_stdout_holds_the_report_alone():
    from arete.application.sync.pipeline import RunStats

    async def chatty(*_a, **_k):
        print("a library that prints")
        return RunStats(2, 1, 0)

    result = _json_sync(chatty)
    assert result.exit_code == 0
    assert json.loads(result.stdout)["changed"] == 1
    assert "a library that prints" in result.stderr


def test_json_errors_exit_1_with_a_message_unless_keep_going():
    from arete.application.sync.pipeline import RunStats

    execute = AsyncMock(return_value=RunStats(3, 1, 2))
    result = _json_sync(execute)
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["errors"] == 2
    assert report["message"] == "2 errors during sync"

    result = _json_sync(execute, keep_going=True)
    assert result.exit_code == 0
    assert json.loads(result.stdout)["ok"] is True


def test_json_a_run_that_stops_early_still_prints_its_reason(caplog):
    from arete.domain.models import AddonOutdated

    outdated = AddonOutdated("Update the arete_ankiconnect add-on and restart Anki.")
    result = _json_sync(AsyncMock(side_effect=outdated))
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "generated": 0,
        "changed": 0,
        "errors": 0,
        "message": "Update the arete_ankiconnect add-on and restart Anki.",
        "prune": None,
    }

    result = _json_sync(AsyncMock(side_effect=RuntimeError("disk on fire")))
    assert result.exit_code == 1
    assert json.loads(result.stdout)["message"] == "disk on fire"
    assert any(r.exc_info for r in caplog.records), "the traceback goes to the log"


def test_the_prompt_accepts_only_yes():
    from arete.interface.cli import _confirm_prune_at_prompt

    for answer, expected in [("yes", True), (" YES ", True), ("y", False), ("", False)]:
        with patch("builtins.input", return_value=answer):
            assert _confirm_prune_at_prompt(None) is expected
