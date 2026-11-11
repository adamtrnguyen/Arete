"""Composition Root.

Centralizes construction of concrete infrastructure implementations.
Composition root: the only package allowed to import infrastructure adapters.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from arete.application.config import AppConfig, require_vault_root

if TYPE_CHECKING:
    from arete.application.stats.service import FsrsStatsService
from arete.application.sync.vault_service import VaultService
from arete.domain.interfaces import AnkiBridge, ContentCache
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from arete.infrastructure.adapters.stats import ConnectStatsRepository, DirectStatsRepository
from arete.infrastructure.persistence.cache import ContentCache as _CacheImpl


async def _ankiconnect_for(config: AppConfig) -> AnkiConnectAdapter | None:
    """The AnkiConnect adapter when this run reaches Anki over AnkiConnect, else None.

    `ankiconnect` always, `direct` never, `auto` when the add-on answers: while Anki is
    open it holds the collection, so the direct backend could not open it. The sync, the
    queue and the stats all decide here, so they cannot disagree.
    """
    if config.backend == "direct":
        return None
    ac = AnkiConnectAdapter(url=config.anki_connect_url or "http://127.0.0.1:8765")
    if config.backend == "ankiconnect" or await ac.is_responsive():
        return ac
    await ac.close()
    return None


async def get_anki_bridge(config: AppConfig) -> AnkiBridge:
    """Return the appropriate AnkiBridge implementation based on config and responsiveness."""
    ac = await _ankiconnect_for(config)
    if config.backend == "auto":
        print(f"Backend: {'AnkiConnect' if ac else 'AnkiDirect'}", file=sys.stderr)
    return ac or AnkiDirectAdapter(anki_base=config.anki_base)


def get_cache(db_path: Path | None = None) -> ContentCache:
    """Return a ContentCache implementation."""
    return _CacheImpl(db_path=db_path)


@contextmanager
def opened_cache(db_path: Path | None = None) -> Iterator[ContentCache]:
    """A ContentCache for the block; its database is closed when the block ends."""
    cache = _CacheImpl(db_path=db_path)
    try:
        yield cache
    finally:
        cache.close()


def get_vault_service(config: AppConfig) -> VaultService:
    """Return the VaultService instance configured for the given app config."""
    vault_root = require_vault_root(config)
    # Same cache as the sync pipeline (orchestrator): config.cache_db or ~/.config/arete/cache.db.
    # A second, vault-local DB would let `vault format` and the servers disagree with `sync`.
    cache = get_cache(Path(config.cache_db) if config.cache_db else None)
    if config.clear_cache:
        cache.clear()
    # `force` = ignore the cache; `clear_cache` = wipe it. Same meaning as in orchestrator.
    return VaultService(vault_root, cache, ignore_cache=config.force)


async def get_stats_repo(config: AppConfig) -> StatsRepository:
    """The stats repository over the backend `get_anki_bridge` would choose."""
    ac = await _ankiconnect_for(config)
    if ac is None:
        return DirectStatsRepository(anki_base=config.anki_base)
    await ac.close()
    return ConnectStatsRepository(url=config.anki_connect_url or "http://localhost:8765")


async def get_stats_service(config: AppConfig) -> FsrsStatsService:
    """Return a fully wired FsrsStatsService instance."""
    from arete.application.stats.metrics_calculator import MetricsCalculator
    from arete.application.stats.service import FsrsStatsService

    repo = await get_stats_repo(config)
    return FsrsStatsService(repo=repo, calculator=MetricsCalculator())
