"""Tests for the main entry point (Components & Cache Clearing)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import RunStats
from arete.application.sync.vault_service import VaultService
from arete.composition.orchestrator import run_sync_logic
from arete.infrastructure.persistence.cache import ContentCache
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
async def test_cache_clearing(mock_setup_logging, mock_run_pipeline, sync_cfg):
    """Test that clear_cache=True wipes pre-existing cache entries before running the pipeline."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(0, 0, 0)

    assert sync_cfg.cache_db is not None
    seeded = ContentCache(db_path=Path(sync_cfg.cache_db))
    seeded.set_notes([(Path("/v/note.md"), 1, "hash123", "{}")])
    assert seeded.get_hash(Path("/v/note.md"), 1) == "hash123"
    seeded.close()

    sync_cfg.clear_cache = True
    await run_sync_logic(sync_cfg)

    call_args = mock_run_pipeline.call_args.args
    assert len(call_args) == 7
    cache = call_args[6]
    assert isinstance(cache, ContentCache)
    check_cache = ContentCache(db_path=Path(sync_cfg.cache_db))
    try:
        assert check_cache.get_hash(Path("/v/note.md"), 1) is None
    finally:
        check_cache.close()


@pytest.mark.asyncio
@patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock)
@patch("arete.composition.orchestrator.setup_logging")
async def test_services_initialization(mock_setup_logging, mock_run_pipeline, sync_cfg):
    """Test that all services are properly initialized and passed to run_pipeline."""
    mock_setup_logging.return_value = (MagicMock(), Path("/tmp/log.txt"), "run-123")
    mock_run_pipeline.return_value = RunStats(0, 0, 0)

    await run_sync_logic(sync_cfg)

    call_args = mock_run_pipeline.call_args.args
    assert len(call_args) == 7
    assert isinstance(call_args[3], VaultService)
    assert isinstance(call_args[4], MarkdownParser)
    assert isinstance(call_args[6], ContentCache)
