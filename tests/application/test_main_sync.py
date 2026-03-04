"""Tests for the main entry point (Success Logic)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arete.application.config import AppConfig
from arete.application.orchestrator import run_sync_logic


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock config for testing."""
    return AppConfig.model_construct(
        root_input=tmp_path,
        vault_root=tmp_path,
        anki_media_dir=tmp_path / "media",
        anki_base=tmp_path / "anki",
        log_dir=tmp_path / "logs",
        backend="auto",
        anki_connect_url="http://localhost:8765",
        apy_bin="apy",
        run_apy=False,
        keep_going=False,
        no_move_deck=False,
        dry_run=False,
        prune=False,
        force=False,
        clear_cache=False,
        workers=2,
        queue_size=100,
        verbose=1,
        show_config=False,
        open_logs=False,
        open_config=False,
    )


@pytest.mark.asyncio
@patch("arete.infrastructure.adapters.anki_connect.AnkiConnectAdapter.is_responsive")
@patch("arete.application.orchestrator.run_pipeline")
@patch("arete.application.orchestrator.setup_logging")
async def test_run_sync_logic_success(
    mock_setup_logging, mock_run_pipeline, mock_is_responsive, mock_config
):
    """Test successful execution of run_sync_logic."""
    # Setup mocks
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")

    # Mock AnkiConnect response to avoid timeout
    mock_is_responsive.return_value = True

    mock_stats = MagicMock()
    mock_stats.files_scanned = 10
    mock_stats.cards_synced = 25
    mock_stats.cards_failed = 0
    mock_stats.total_errors = 0
    mock_stats.total_generated = 25
    mock_stats.total_imported = 25
    mock_run_pipeline.return_value = mock_stats

    # Execute
    await run_sync_logic(mock_config)

    # Verify logging was set up
    mock_setup_logging.assert_called_once_with(mock_config.log_dir, mock_config.verbose)

    # Verify pipeline was called
    mock_run_pipeline.assert_called_once()
    # run_pipeline(config, logger, run_id, vault_service, parser, anki_bridge, cache)
    call_args = mock_run_pipeline.call_args.args
    assert call_args[0] == mock_config  # config
    assert call_args[1] == mock_logger  # logger
    assert call_args[2] == "run-123"  # run_id


@pytest.mark.asyncio
@patch("arete.application.orchestrator.run_pipeline")
@patch("arete.application.orchestrator.setup_logging")
async def test_summary_output(mock_setup_logging, mock_run_pipeline, mock_config, capsys):
    """Test that summary is printed after pipeline execution."""
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")

    # Mock stats
    mock_stats = MagicMock()
    mock_stats.files_scanned = 10
    mock_stats.cards_synced = 25
    mock_stats.cards_failed = 2
    mock_stats.total_errors = 0  # Don't trigger sys.exit
    mock_stats.total_generated = 27
    mock_stats.total_imported = 25
    mock_run_pipeline.return_value = mock_stats

    # run_sync_logic now uses logging.getLogger("arete.application.orchestrator") for the summary log,
    # so we need to mock that specific logger.
    mock_summary_logger = MagicMock()

    with (
        patch(
            "arete.infrastructure.adapters.anki_connect.AnkiConnectAdapter.is_responsive",
            return_value=True,
        ),
        patch("logging.getLogger", return_value=mock_summary_logger),
    ):
        await run_sync_logic(mock_config)

    # Verify summary was logged on the summary logger
    summary_calls = [
        call for call in mock_summary_logger.info.call_args_list if "=== summary ===" in str(call)
    ]
    assert len(summary_calls) > 0
    summary_msg = str(summary_calls[0])
    assert "generated=27" in summary_msg
    assert "updated/added=25" in summary_msg
