from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from arete.application.config import AppConfig
from arete.application.sync.pipeline import run_pipeline
from arete.domain.models import AnkiNote


@pytest.fixture
def config():
    return AppConfig(vault_root=Path("/v"), anki_media_dir=Path("/m"), root_input=Path("/v"))


@pytest.mark.asyncio
async def test_pipeline_producer_error(config):
    # Mock components
    logger = MagicMock()
    vault = MagicMock()
    parser = MagicMock()
    bridge = AsyncMock()
    cache = MagicMock()

    # Vault yields one file: (path, meta, fresh)
    vault.scan_for_compatible_files.return_value = [(Path("/v/test.md"), {}, True)]

    # Parser raises Exception
    parser.parse_file.side_effect = Exception("Parser Boom")

    stats = await run_pipeline(config, logger, "runid", vault, parser, bridge, cache)

    assert stats.total_errors >= 1
    logger.error.assert_called_with(ANY)


@pytest.mark.asyncio
async def test_pipeline_consumer_error(config):
    logger = MagicMock()
    vault = MagicMock()
    parser = MagicMock()
    bridge = AsyncMock()
    cache = MagicMock()

    vault.scan_for_compatible_files.return_value = [(Path("/v/test.md"), {}, True)]

    # Parser yields one note
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "F", "Back": "B"},
        tags=[],
        start_line=1,
        end_line=2,
        source_file=Path("/v/test.md"),
        source_index=1,
        content_hash="h1",
    )
    # Fix: Return (notes, skipped, inventory)
    parser.parse_file.return_value = ([note], [], [note])

    # Bridge raises Exception
    bridge.sync_notes.side_effect = Exception("Bridge Boom")

    stats = await run_pipeline(config, logger, "runid", vault, parser, bridge, cache)

    assert stats.total_errors >= 1
    logger.error.assert_called()
