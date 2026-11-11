"""Sync orchestration — wires up services and runs the pipeline."""

import asyncio
import contextlib
import logging
import platform
import signal
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

from arete.application.config import AppConfig, VaultRootUnknown, require_vault_root
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import RunStats, run_pipeline
from arete.application.sync.prune import PruneConfirm
from arete.application.sync.vault_service import VaultService
from arete.application.utils.logging import close_run_log, setup_logging
from arete.composition.factory import get_anki_bridge, opened_cache
from arete.domain.interfaces import ContentCache
from arete.domain.models import AddonOutdated
from arete.infrastructure.persistence.cache import cache_path
from arete.infrastructure.persistence.sync_lock import LockHeld, exclusive_lock, lock_path_for

# The exit status of a sync refused because another sync holds the lock (EX_TEMPFAIL).
EXIT_SYNC_LOCKED = 75
SYNC_LOCKED_MESSAGE = "Another Arete sync is running, so this sync did nothing. Run it again after."


# The exit status of a sync stopped by SIGTERM (128 + 15, as a shell reports it).
EXIT_SYNC_TERMINATED = 143
SYNC_TERMINATED_MESSAGE = (
    "Sync terminated: it stopped between steps, so every file it wrote is whole. "
    "Run it again to finish."
)


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
    require_vault_root(config)
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
    """The run, with its log file and cache database open for exactly its length."""
    logger, _main_log_path, run_id = setup_logging(config.log_dir, config.verbose)
    try:
        with opened_cache(cache_file) as cache:
            stats = await _run(config, cache, logger, run_id, confirm_prune, prompt)
        pruned = f" prune: {stats.prune.summary()}" if stats.prune else ""
        logging.getLogger("arete.main").info(
            f"=== summary === generated={stats.total_generated} "
            f"updated/added={stats.total_imported} errors={stats.total_errors}{pruned}"
        )
        return stats
    except AddonOutdated as e:
        logging.getLogger("arete.main").error(f"[sync] stopped: {e}")
        raise
    finally:
        close_run_log(logger)


async def _run(
    config: AppConfig,
    cache: ContentCache,
    logger: logging.Logger,
    run_id: str,
    confirm_prune: bool,
    prompt: PruneConfirm | None,
) -> RunStats:
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

    if config.clear_cache:
        logger.info("Clearing content cache as requested...")
        cache.clear()

    if config.root_input is None:
        raise ValueError("config.root_input must be set before sync")
    vault_service = VaultService(config.root_input, cache, ignore_cache=config.force)

    vault_root = require_vault_root(config)
    if config.anki_media_dir is None:
        raise ValueError("config.anki_media_dir must be set before sync")

    parser = MarkdownParser(
        vault_root,
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
    `EXIT_SYNC_LOCKED`, so a caller can tell it from a failed sync (exit 1). SIGTERM stops
    the sync at its next await (`sigterm_cancels`): cleanup runs, the lock is released,
    and it exits with `EXIT_SYNC_TERMINATED`.
    """
    logger = logging.getLogger("arete.main")
    try:
        with sigterm_cancels():
            stats = await execute_sync(config, confirm_prune=confirm_prune, prompt=prompt)
    except asyncio.CancelledError as e:
        print(SYNC_TERMINATED_MESSAGE, file=sys.stderr)
        raise SystemExit(EXIT_SYNC_TERMINATED) from e
    except SyncLocked as e:
        print(e, file=sys.stderr)
        raise SystemExit(EXIT_SYNC_LOCKED) from e
    except VaultRootUnknown as e:
        logger.error(f"[sync] {e}")
        raise SyncFailedError(str(e)) from e
    except AddonOutdated as e:  # logged by the run, into its log file
        raise SyncFailedError(str(e)) from e

    if stats.total_errors and not config.keep_going:
        raise SyncFailedError(f"{stats.total_errors} errors during sync")


@contextlib.contextmanager
def sigterm_cancels() -> Iterator[None]:
    """While the block runs, SIGTERM cancels the running task instead of killing the process.

    The cancellation lands at the task's next await. No vault write awaits, so a file is
    never left half written by it, and `finally` blocks (the bridge, the cache, the lock)
    run. Where the loop cannot take a signal handler (Windows, or off the main thread)
    SIGTERM keeps its default.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    try:
        loop.add_signal_handler(signal.SIGTERM, task.cancel if task else lambda: None)
    except (NotImplementedError, RuntimeError, ValueError):
        yield
        return
    try:
        yield
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
