from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from arete.application.config import AppConfig
from arete.application.sync.pipeline import run_pipeline
from arete.domain.models import AnkiNote
from tests.fakes.pipeline import note_sync_factory_for


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

    stats = await run_pipeline(
        config,
        logger,
        "runid",
        vault,
        parser,
        bridge,
        cache,
        note_sync_factory=note_sync_factory_for(bridge),
    )

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
        source_file=Path("/v/test.md"),
        source_index=1,
        content_hash="h1",
    )
    # parse_file returns (notes, skipped, inventory)
    parser.parse_file.return_value = ([note], [], [note])

    # Bridge raises Exception
    bridge.sync_notes.side_effect = Exception("Bridge Boom")

    stats = await run_pipeline(
        config,
        logger,
        "runid",
        vault,
        parser,
        bridge,
        cache,
        note_sync_factory=note_sync_factory_for(bridge),
    )

    assert stats.total_errors >= 1
    logger.error.assert_called()


@pytest.mark.asyncio
async def test_pipeline_records_unreadable_files_as_errors_and_runs_prune_at_zero_compatible(
    tmp_path,
):
    """Unreadable Arete files fail normal syncs, and 0 compatible files still run Stage 5 prune."""
    from arete.application.sync.parser import MarkdownParser
    from arete.application.sync.vault_service import VaultService
    from arete.infrastructure.persistence.cache import ContentCache

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "broken.md").write_text("---\narete: true\ncards: [\n---\n", encoding="utf-8")

    cfg = AppConfig(
        vault_root=vault_dir,
        anki_media_dir=tmp_path / "media",
        root_input=vault_dir,
        prune=False,
    )
    cache = ContentCache(tmp_path / "c.db")
    vs = VaultService(vault_dir, cache)
    parser = MarkdownParser(vault_dir, tmp_path / "media")
    bridge = AsyncMock()
    logger = MagicMock()

    stats = await run_pipeline(cfg, logger, "r1", vs, parser, bridge, cache)
    assert stats.total_errors == 1

    # With --prune on an unreadable-only vault, prune must be refused rather than skipped (None).
    cfg_prune = AppConfig(
        vault_root=vault_dir,
        anki_media_dir=tmp_path / "media",
        root_input=vault_dir,
        prune=True,
    )
    stats_refused = await run_pipeline(cfg_prune, logger, "r2", vs, parser, bridge, cache)
    assert stats_refused.prune is not None and stats_refused.prune.status == "refused"

    # When the last Arete file is removed cleanly, 0 compatible files still plans/runs prune.
    (vault_dir / "broken.md").unlink()
    bridge.get_deck_names = AsyncMock(return_value=["Default"])
    stats_clean = await run_pipeline(cfg_prune, logger, "r3", vs, parser, bridge, cache)
    assert stats_clean.total_errors == 0
    assert stats_clean.prune is not None and stats_clean.prune.status == "clean"
    cache.close()


@pytest.mark.asyncio
async def test_pipeline_propagates_addon_outdated_from_consumer(config):
    """AddonOutdated raised inside a consumer stops the whole pipeline immediately."""
    from arete.domain.models import AddonOutdated

    logger = MagicMock()
    vault = MagicMock()
    vault.unreadable = []
    parser = MagicMock()
    parser.declared_id.return_value = None
    parser.extract_raw_nid.return_value = None
    bridge = AsyncMock()
    cache = MagicMock()

    vault.scan_for_compatible_files.return_value = [(Path("/v/test.md"), {}, True)]
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "F", "Back": "B"},
        tags=[],
        start_line=1,
        source_file=Path("/v/test.md"),
        source_index=1,
        content_hash="h1",
    )
    parser.parse_file.return_value = ([note], [], [])
    bridge.sync_notes.side_effect = AddonOutdated("Update arete_ankiconnect")

    with pytest.raises(AddonOutdated, match="Update arete_ankiconnect"):
        await run_pipeline(
            config,
            logger,
            "runid",
            vault,
            parser,
            bridge,
            cache,
            note_sync_factory=note_sync_factory_for(bridge),
        )


@pytest.mark.asyncio
async def test_pipeline_cancellation_drains_workers_before_unwinding(config):
    """Cancelling run_pipeline cancels and awaits active consumer tasks before returning."""
    import asyncio

    logger = MagicMock()
    vault = MagicMock()
    vault.unreadable = []
    parser = MagicMock()
    parser.declared_id.return_value = None
    parser.extract_raw_nid.return_value = None
    bridge = AsyncMock()
    cache = MagicMock()

    vault.scan_for_compatible_files.return_value = [(Path("/v/test.md"), {}, True)]
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "F", "Back": "B"},
        tags=[],
        start_line=1,
        source_file=Path("/v/test.md"),
        source_index=1,
        content_hash="h1",
    )
    parser.parse_file.return_value = ([note], [], [])

    entered = asyncio.Event()
    consumer_cleaned = False

    async def slow_sync(_batch):
        nonlocal consumer_cleaned
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            consumer_cleaned = True
        return []

    bridge.sync_notes.side_effect = slow_sync

    task = asyncio.create_task(
        run_pipeline(
            config,
            logger,
            "runid",
            vault,
            parser,
            bridge,
            cache,
            note_sync_factory=note_sync_factory_for(bridge),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert consumer_cleaned is True
