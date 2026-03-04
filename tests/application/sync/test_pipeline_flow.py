import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from arete.application.config import AppConfig
from arete.application.sync.pipeline import UpdateItem, run_pipeline
from arete.domain.models import AnkiNote
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter


@pytest.fixture
def mock_components():
    logger = MagicMock()
    vault = MagicMock()
    parser = MagicMock()
    bridge = AsyncMock()
    cache = MagicMock()

    # Setup mocks
    # Return 3-tuple: path, meta, is_fresh=True
    vault.scan_for_compatible_files.return_value = [
        (Path("/vault/test.md"), {"deck": "Default"}, True)
    ]

    # Mock parser to return one note and one inventory item
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Q", "Back": "A"},
        tags=[],
        start_line=1,
        end_line=10,
        source_file=Path("/vault/test.md"),
        source_index=1,
    )
    # notes, skipped_indices, inventory
    parser.parse_file.return_value = ([note], [], [{"deck": "Default"}])

    # Mock bridge sync
    bridge.sync_notes.return_value = [
        UpdateItem(
            source_file=Path("/vault/test.md"),
            source_index=1,
            new_nid="123",
            new_cid="456",
            ok=True,
            note=note,
        )
    ]
    bridge.get_deck_names.return_value = ["Default"]

    return logger, vault, parser, bridge, cache


@pytest.mark.asyncio
async def test_run_pipeline_success(mock_components, tmp_path):
    logger, vault, parser, bridge, cache = mock_components

    config = AppConfig.model_construct(
        vault_root=tmp_path,
        queue_size=10,
        workers=1,
        prune=False,
    )

    stats = await run_pipeline(
        config=config,
        logger=logger,
        run_id="test_run",
        vault_service=vault,
        parser=parser,
        anki_bridge=bridge,
        cache=cache,
    )

    print(f"DEBUG: logger calls: {logger.method_calls}")
    assert stats.total_generated == 1
    assert stats.total_imported == 1
    assert stats.total_errors == 0
    bridge.sync_notes.assert_called()
    vault.apply_updates.assert_called()


@pytest.mark.asyncio
async def test_run_pipeline_no_files(mock_components, tmp_path):
    logger, vault, parser, bridge, cache = mock_components
    vault.scan_for_compatible_files.return_value = []

    config = AppConfig.model_construct(vault_root=tmp_path, queue_size=10, workers=1, prune=False)

    stats = await run_pipeline(config, logger, "run", vault, parser, bridge, cache)
    assert stats.total_generated == 0


@pytest.mark.asyncio
async def test_run_pipeline_consumer_error(mock_components, tmp_path):
    logger, vault, parser, bridge, cache = mock_components

    # Simulate bridge failure
    bridge.sync_notes.side_effect = Exception("Bridge Crash")

    config = AppConfig.model_construct(vault_root=tmp_path, queue_size=10, workers=1, prune=False)

    stats = await run_pipeline(config, logger, "run", vault, parser, bridge, cache)

    # Expect 0 generated because it crashed before appending to updates?
    # Or handled by exception block?
    # In pipeline consumer:
    # except Exception as e: logger.error... recorder.add_error...
    # updates list is NOT appended to in exception block unless we mock sync_notes to return error UpdateItem

    # If side_effect raises exception, it goes to except block
    # updates list remains empty.

    assert stats.total_generated == 0
    assert logger.error.called


@pytest.mark.asyncio
async def test_run_pipeline_partial_sync_failure(mock_components, tmp_path):
    logger, vault, parser, bridge, cache = mock_components

    note = parser.parse_file.return_value[0][0]
    # Sync returns failure item
    bridge.sync_notes.return_value = [
        UpdateItem(
            source_file=Path("/vault/test.md"),
            source_index=1,
            new_nid=None,
            new_cid=None,
            ok=False,
            error="Sync Failed",
            note=note,
        )
    ]

    config = AppConfig.model_construct(vault_root=tmp_path, queue_size=10, workers=1, prune=False)

    stats = await run_pipeline(config, logger, "run", vault, parser, bridge, cache)

    assert stats.total_generated == 1  # We generated an attempt
    assert stats.total_imported == 0
    assert stats.total_errors == 1


@pytest.mark.asyncio
async def test_run_pipeline_with_apy_sequential(tmp_path):
    """AnkiDirectAdapter (SQLite) forces sequential processing."""
    config = AppConfig(vault_root=tmp_path, workers=4)
    vault_service = MagicMock()
    vault_service.scan_for_compatible_files.return_value = []
    logger = logging.getLogger("test_pipeline")

    bridge = MagicMock(spec=AnkiDirectAdapter)

    await run_pipeline(config, logger, "run1", vault_service, MagicMock(), bridge, MagicMock())
