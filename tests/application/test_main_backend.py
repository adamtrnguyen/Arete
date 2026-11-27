"""Tests for the main entry point (Backend Selection)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.application.sync.pipeline import RunStats
from arete.composition.orchestrator import run_sync_logic
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from tests.factories import make_config


@pytest.fixture
def sync_cfg(tmp_path):
    """Create a validated AppConfig pointed inside tmp_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return make_config(vault, backend="auto")


@pytest.mark.asyncio
@patch(
    "arete.infrastructure.adapters.anki_connect.AnkiConnectAdapter.is_responsive",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_backend_selection_ankiconnect(
    mock_setup_logging, mock_run_pipeline, mock_is_responsive, sync_cfg
):
    """Test that AnkiConnect is selected when available."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(0, 0, 0)

    sync_cfg.backend = "auto"
    await run_sync_logic(sync_cfg)

    mock_is_responsive.assert_called()
    call_args = mock_run_pipeline.call_args.args
    assert isinstance(call_args[5], AnkiConnectAdapter)


@pytest.mark.asyncio
@patch(
    "arete.infrastructure.adapters.anki_connect.AnkiConnectAdapter.is_responsive",
    new_callable=AsyncMock,
    return_value=False,
)
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_backend_selection_direct_fallback(
    mock_setup_logging, mock_run_pipeline, mock_is_responsive, sync_cfg
):
    """Test fallback to AnkiDirectAdapter when AnkiConnect is unavailable."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(0, 0, 0)

    sync_cfg.backend = "auto"
    await run_sync_logic(sync_cfg)

    mock_is_responsive.assert_called()
    call_args = mock_run_pipeline.call_args.args
    assert isinstance(call_args[5], AnkiDirectAdapter)


@pytest.mark.asyncio
@patch("arete.composition.factory.AnkiConnectAdapter")
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_backend_manual_ankiconnect_and_fallback(
    mock_setup_logging, mock_run_pipeline, mock_anki_connect_adapter_cls, sync_cfg
):
    """Test manual selection of AnkiConnect backend and auto fallback to direct."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(0, 0, 0)

    mock_ac_instance = MagicMock()
    mock_ac_instance.is_responsive = AsyncMock(return_value=True)
    mock_ac_instance.close = AsyncMock()
    mock_anki_connect_adapter_cls.return_value = mock_ac_instance

    # 1. Force AnkiConnect
    sync_cfg.backend = "ankiconnect"
    await run_sync_logic(sync_cfg)
    assert mock_run_pipeline.call_args_list[0].args[5] is mock_ac_instance

    # 2. Auto fallback when AnkiConnect is unresponsive
    mock_ac_instance.is_responsive = AsyncMock(return_value=False)
    sync_cfg.backend = "auto"
    await run_sync_logic(sync_cfg)
    assert isinstance(mock_run_pipeline.call_args_list[1].args[5], AnkiDirectAdapter)


@pytest.mark.asyncio
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_backend_manual_direct(mock_setup_logging, mock_run_pipeline, sync_cfg):
    """Test manual selection of direct backend."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(0, 0, 0)

    sync_cfg.backend = "direct"
    await run_sync_logic(sync_cfg)

    call_args = mock_run_pipeline.call_args.args
    assert isinstance(call_args[5], AnkiDirectAdapter)
