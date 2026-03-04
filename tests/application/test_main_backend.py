"""Tests for the main entry point (Backend Selection)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_backend_selection_ankiconnect(
    mock_setup_logging, mock_run_pipeline, mock_is_responsive, mock_config
):
    """Test that AnkiConnect is selected when available."""
    # Setup mocks
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")

    # Mock successful AnkiConnect response
    # It must be awaitable
    mock_is_responsive.return_value = True
    if not isinstance(mock_is_responsive, AsyncMock):
        # Force it to be an AsyncMock for await support
        mock_is_responsive.side_effect = AsyncMock(return_value=True)

    # run_pipeline must return an awaitable. Since it returns `Stats` object, we wrap it.
    mock_pipeline_stats = MagicMock(
        files_scanned=0, total_errors=0, total_generated=0, total_imported=0
    )
    # mock_run_pipeline is called with await, so it must return a coroutine.
    if not isinstance(mock_run_pipeline, AsyncMock):
        mock_run_pipeline.side_effect = AsyncMock(return_value=mock_pipeline_stats)
    else:
        mock_run_pipeline.return_value = mock_pipeline_stats

    # Execute with auto backend
    mock_config.backend = "auto"
    await run_sync_logic(mock_config)

    # Verify AnkiConnect was tested
    mock_is_responsive.assert_called()

    # Verify pipeline was called with AnkiConnect adapter
    call_args = mock_run_pipeline.call_args.args
    from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

    assert isinstance(call_args[5], AnkiConnectAdapter)  # anki_bridge is 6th arg


@pytest.mark.asyncio
@patch("arete.infrastructure.adapters.anki_connect.AnkiConnectAdapter.is_responsive")
@patch("arete.application.orchestrator.run_pipeline")
@patch("arete.application.orchestrator.setup_logging")
async def test_backend_selection_apy_fallback(
    mock_setup_logging, mock_run_pipeline, mock_is_responsive, mock_config
):
    """Test fallback to apy when AnkiConnect is unavailable."""
    # Setup mocks
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")

    # Mock AnkiConnect failure
    mock_is_responsive.return_value = False
    if not isinstance(mock_is_responsive, AsyncMock):
        mock_is_responsive.side_effect = AsyncMock(return_value=False)

    mock_pipeline_stats = MagicMock(
        files_scanned=0, total_errors=0, total_generated=0, total_imported=0
    )
    if not isinstance(mock_run_pipeline, AsyncMock):
        mock_run_pipeline.side_effect = AsyncMock(return_value=mock_pipeline_stats)
    else:
        mock_run_pipeline.return_value = mock_pipeline_stats

    # Execute with auto backend
    mock_config.backend = "auto"
    await run_sync_logic(mock_config)

    # Verify pipeline was called with AnkiDirect adapter (fallback)
    call_args = mock_run_pipeline.call_args.args
    from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter

    assert isinstance(call_args[5], AnkiDirectAdapter)  # anki_bridge is 6th arg


@pytest.mark.asyncio
@patch("arete.application.factory.AnkiConnectAdapter")
@patch("arete.application.orchestrator.run_pipeline")
@patch("arete.application.orchestrator.setup_logging")
async def test_backend_manual_ankiconnect_and_fallback(
    mock_setup_logging, mock_run_pipeline, mock_anki_connect_adapter_cls, mock_config
):
    """Test manual selection of AnkiConnect backend and its fallback."""
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")

    mock_pipeline_stats = MagicMock(
        files_scanned=0, total_errors=0, total_generated=0, total_imported=0
    )
    if not isinstance(mock_run_pipeline, AsyncMock):
        mock_run_pipeline.side_effect = AsyncMock(return_value=mock_pipeline_stats)
    else:
        mock_run_pipeline.return_value = mock_pipeline_stats

    # Mock an instance of AnkiConnectAdapter
    mock_ac_instance = MagicMock()
    # is_responsive is an async method on the instance
    mock_ac_instance.is_responsive = AsyncMock(return_value=True)
    mock_ac_instance.close = AsyncMock()
    mock_anki_connect_adapter_cls.return_value = mock_ac_instance

    # 1. Force AnkiConnect (should succeed)
    mock_config.backend = "ankiconnect"
    await run_sync_logic(mock_config)

    call_args = mock_run_pipeline.call_args_list[0].args
    # We cannot use isinstance because the class is mocked
    assert call_args[5] is mock_ac_instance  # anki_bridge is 6th arg

    # 2. Fallback (AnkiConnect not responsive -> Anki Direct)
    from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter

    mock_ac_instance.is_responsive = AsyncMock(return_value=False)
    # So let's test AUTO fallback.
    mock_config.backend = "auto"
    await run_sync_logic(mock_config)
    call_args = mock_run_pipeline.call_args_list[1].args  # This is the second call to run_pipeline
    assert isinstance(call_args[5], AnkiDirectAdapter)  # Should use Direct


@pytest.mark.asyncio
@patch("arete.application.orchestrator.run_pipeline")
@patch("arete.application.orchestrator.setup_logging")
async def test_backend_manual_direct(mock_setup_logging, mock_run_pipeline, mock_config):
    """Test manual selection of direct backend."""
    mock_logger = MagicMock()
    mock_setup_logging.return_value = (mock_logger, Path("/tmp/log.txt"), "run-123")

    mock_pipeline_stats = MagicMock(
        files_scanned=0, total_errors=0, total_generated=0, total_imported=0
    )
    if not isinstance(mock_run_pipeline, AsyncMock):
        mock_run_pipeline.side_effect = AsyncMock(return_value=mock_pipeline_stats)
    else:
        mock_run_pipeline.return_value = mock_pipeline_stats

    # Force direct
    mock_config.backend = "direct"
    await run_sync_logic(mock_config)

    # Verify AnkiDirect was used
    call_args = mock_run_pipeline.call_args.args
    from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter

    assert isinstance(call_args[5], AnkiDirectAdapter)  # anki_bridge is 6th arg
