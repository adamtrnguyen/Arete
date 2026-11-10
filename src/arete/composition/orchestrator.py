"""Sync orchestration — wires up services and runs the pipeline."""

import logging
import platform
import sys
from contextlib import ExitStack
from pathlib import Path

from arete.application.config import AppConfig
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import RunStats, run_pipeline
from arete.application.sync.prune import PruneConfirm
from arete.application.sync.vault_service import VaultService
from arete.application.utils.logging import setup_logging
from arete.composition.factory import get_anki_bridge, get_cache
from arete.domain.models import AddonOutdated
from arete.infrastructure.persistence.cache import cache_path
from arete.infrastructure.persistence.sync_lock import LockHeld, exclusive_lock, lock_path_for

# The exit status of a sync refused because another sync holds the lock (EX_TEMPFAIL).
EXIT_SYNC_LOCKED = 75
SYNC_LOCKED_MESSAGE = "Another Arete sync is running, so this sync did nothing. Run it again after."


class SyncLocked(Exception):
    """Another sync holds the lock on this run's cache; this run read and wrote nothing."""

    def __init__(self, lock_file: Path):
        """The one-line message, naming the lock file."""
        super().__init__(f"{SYNC_LOCKED_MESSAGE} (lock: {lock_file})")
        self.lock_file = lock_file


async def execute_sync(
    config: AppConfig, *, confirm_prune: bool = False, prompt: PruneConfirm | None = None
) -> RunStats:
    """Core sync execution logic. Returns stats object instead of exiting.

    `confirm_prune` and `prompt` are per-run consent to delete; they are deliberately
    not AppConfig fields, so no config file or environment variable can pre-approve one.

    One sync at a time per cache database: the lock beside it is taken before anything is
    read and held to the end, or `SyncLocked` is raised at once. A dry run takes it too:
    its plan, read while another sync writes, would describe neither state.
    """
    cache_file = cache_path(Path(config.cache_db) if config.cache_db else None)
    with ExitStack() as held:
        try:
            held.enter_context(exclusive_lock(lock_path_for(cache_file)))
        except LockHeld as e:
            raise SyncLocked(e.path) from e
        return await _execute_locked(config, cache_file, confirm_prune, prompt)


async def _execute_locked(
    config: AppConfig, cache_file: Path, confirm_prune: bool, prompt: PruneConfirm | None
) -> RunStats:
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

    cache = get_cache(db_path=cache_file)

    if config.clear_cache:
        logger.info("Clearing content cache as requested...")
        cache.clear()

    if config.root_input is None:
        raise ValueError("config.root_input must be set before sync")
    vault_service = VaultService(config.root_input, cache, ignore_cache=config.force)

    if config.vault_root is None:
        raise ValueError("config.vault_root must be set before sync")
    if config.anki_media_dir is None:
        raise ValueError("config.anki_media_dir must be set before sync")

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
            config,
            logger,
            run_id,
            vault_service,
            parser,
            anki_bridge,
            cache,
            confirm_prune=confirm_prune,
            prompt=prompt,
        )
        return stats
    finally:
        await anki_bridge.close()


class SyncFailedError(Exception):
    """Raised when sync completes with errors and keep_going is False."""


async def run_sync_logic(
    config: AppConfig, *, confirm_prune: bool = False, prompt: PruneConfirm | None = None
):
    """Orchestrates the sync process using the provided config.

    A sync refused by the lock prints its one-line message on stderr and exits with
    `EXIT_SYNC_LOCKED`, so a caller can tell it from a failed sync (exit 1).
    """
    logger = logging.getLogger("arete.main")
    try:
        stats = await execute_sync(config, confirm_prune=confirm_prune, prompt=prompt)
    except SyncLocked as e:
        print(e, file=sys.stderr)
        raise SystemExit(EXIT_SYNC_LOCKED) from e
    except AddonOutdated as e:
        logger.error(f"[sync] stopped: {e}")
        raise SyncFailedError(str(e)) from e

    logger.info(
        f"=== summary === generated={stats.total_generated} "
        f"updated/added={stats.total_imported} errors={stats.total_errors}"
    )

    if stats.total_errors and not config.keep_going:
        raise SyncFailedError(f"{stats.total_errors} errors during sync")
