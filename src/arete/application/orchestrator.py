"""Sync orchestration — wires up services and runs the pipeline."""

import logging
import platform
import sys
from pathlib import Path

from arete.application.config import AppConfig
from arete.application.factory import get_anki_bridge, get_cache
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import RunStats, run_pipeline
from arete.application.utils.logging import setup_logging
from arete.application.sync.vault_service import VaultService


async def execute_sync(config: AppConfig) -> RunStats:
    """Core sync execution logic. Returns stats object instead of exiting."""
    logger, main_log_path, run_id = setup_logging(config.log_dir, config.verbose)
    logger.info(f"=== obsidian → anki (run_id={run_id}) ===")
    logger.info(f"System: {platform.system()} {platform.release()} ({platform.machine()})")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"anki_media_dir={config.anki_media_dir}")
    if config.anki_base:
        logger.info(f"anki_base={config.anki_base}")
    logger.info(f"vault_root={config.vault_root}")
    logger.info(f"Starting sync for vault: {config.vault_root}")
    logger.debug(
        f"[main] Config: root_input={config.root_input}, backend={config.backend}, "
        f"verbose={config.verbose}"
    )

    cache_path = Path(config.cache_db) if config.cache_db else None
    cache = get_cache(db_path=cache_path)

    if config.clear_cache:
        logger.info("Clearing content cache as requested...")
        cache.clear()

    assert config.root_input is not None
    vault_service = VaultService(config.root_input, cache, ignore_cache=config.force)

    assert config.vault_root is not None
    assert config.anki_media_dir is not None

    parser = MarkdownParser(
        config.vault_root,
        config.anki_media_dir,
        ignore_cache=config.force,
        default_deck=config.default_deck,
        logger=logger,
    )

    anki_bridge = await get_anki_bridge(config)
    try:
        stats = await run_pipeline(
            config, logger, run_id, vault_service, parser, anki_bridge, cache
        )
        return stats
    finally:
        await anki_bridge.close()


async def run_sync_logic(config: AppConfig):
    """Orchestrates the sync process using the provided config."""
    stats = await execute_sync(config)
    logger = logging.getLogger("arete.main")

    logger.info(
        f"=== summary === generated={stats.total_generated} "
        f"updated/added={stats.total_imported} errors={stats.total_errors}"
    )

    if stats.total_errors and not config.keep_going:
        sys.exit(1)
