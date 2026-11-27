"""Tests for the main entry point (Success Logic)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.application.sync.pipeline import RunStats
from arete.application.sync.prune import OrphanNote, PruneReport
from arete.composition.orchestrator import run_sync_logic
from tests.factories import make_config


@pytest.fixture
def sync_cfg(tmp_path):
    """Create a validated AppConfig pointed inside tmp_path using the direct backend."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return make_config(vault, backend="direct")


@pytest.mark.asyncio
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_run_sync_logic_success(mock_setup_logging, mock_run_pipeline, sync_cfg):
    """Test successful execution of run_sync_logic."""
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(total_generated=25, total_imported=25, total_errors=0)

    await run_sync_logic(sync_cfg)

    mock_setup_logging.assert_called_once_with(sync_cfg.log_dir, sync_cfg.verbose)
    mock_run_pipeline.assert_called_once()
    call_args = mock_run_pipeline.call_args.args
    assert call_args[0] == sync_cfg
    assert call_args[1] == mock_logger
    assert call_args[2] == "run-123"


@pytest.mark.asyncio
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_summary_output(mock_setup_logging, mock_run_pipeline, sync_cfg):
    """Test that summary is printed after pipeline execution."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(total_generated=27, total_imported=25, total_errors=0)
    mock_summary_logger = MagicMock()

    with patch("logging.getLogger", return_value=mock_summary_logger):
        await run_sync_logic(sync_cfg)

    summary_calls = [
        call for call in mock_summary_logger.info.call_args_list if "=== summary ===" in str(call)
    ]
    assert len(summary_calls) > 0
    summary_msg = str(summary_calls[0])
    assert "generated=27" in summary_msg
    assert "updated/added=25" in summary_msg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report", "said"),
    [
        (
            PruneReport("deleted", notes=[OrphanNote(1, "arete_x", "D")]),
            "deleted 1 notes and 0 decks",
        ),
        (PruneReport("unconfirmed", decks=["D"]), "not confirmed, deleted nothing"),
        (
            PruneReport("withheld", notes=[OrphanNote(1, "arete_x", "D")]),
            "withheld, the run had errors",
        ),
    ],
)
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_the_summary_line_states_the_prune_outcome(
    mock_setup_logging, mock_run_pipeline, sync_cfg, report, said
):
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(3, 3, 0, prune=report)
    summary_logger = MagicMock()

    with patch("logging.getLogger", return_value=summary_logger):
        await run_sync_logic(sync_cfg)

    [line] = [str(c) for c in summary_logger.info.call_args_list if "=== summary ===" in str(c)]
    assert f"prune: {said}" in line
