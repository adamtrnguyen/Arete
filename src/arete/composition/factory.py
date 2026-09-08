"""Composition Root.

Centralizes construction of concrete infrastructure implementations.
Composition root: the only package allowed to import infrastructure adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from arete.application.config import AppConfig

if TYPE_CHECKING:
    from arete.application.stats.service import FsrsStatsService
from arete.application.sync.vault_service import VaultService
from arete.domain.interfaces import AnkiBridge, ContentCache
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from arete.infrastructure.adapters.stats import ConnectStatsRepository, DirectStatsRepository
from arete.infrastructure.persistence.cache import ContentCache as _CacheImpl


async def get_anki_bridge(config: AppConfig) -> AnkiBridge:
    """Return the appropriate AnkiBridge implementation based on config and responsiveness."""
    # 1. Manual selection
    if config.backend == "ankiconnect":
        return AnkiConnectAdapter(url=config.anki_connect_url)

    if config.backend == "direct":
        return AnkiDirectAdapter(anki_base=config.anki_base)

    # 2. Auto selection
    ac = AnkiConnectAdapter(url=config.anki_connect_url)
    if await ac.is_responsive():
        import sys

        print("Backend: AnkiConnect", file=sys.stderr)
        return ac

    import sys

    print("Backend: AnkiDirect", file=sys.stderr)
    return AnkiDirectAdapter(anki_base=config.anki_base)


def get_cache(db_path: Path | None = None) -> ContentCache:
    """Return a ContentCache implementation."""
    return _CacheImpl(db_path=db_path)


def get_vault_service(config: AppConfig) -> VaultService:
    """Return the VaultService instance configured for the given app config."""
    if config.vault_root is None:
        raise ValueError("vault_root is required for VaultService")
    # Same cache as the sync pipeline (orchestrator): config.cache_db or ~/.config/arete/cache.db.
    # A second, vault-local DB would let `vault format` and the servers disagree with `sync`.
    cache = get_cache(Path(config.cache_db) if config.cache_db else None)
    return VaultService(config.vault_root, cache, ignore_cache=config.clear_cache)


def get_stats_repo(config: AppConfig) -> StatsRepository:
    """Return the appropriate StatsRepository implementation based on config."""
    if config.backend == "ankiconnect":
        url = config.anki_connect_url or "http://localhost:8765"
        return ConnectStatsRepository(url=url)
    return DirectStatsRepository(anki_base=config.anki_base)


def get_stats_service(config: AppConfig) -> FsrsStatsService:
    """Return a fully wired FsrsStatsService instance."""
    from arete.application.stats.metrics_calculator import MetricsCalculator
    from arete.application.stats.service import FsrsStatsService

    repo = get_stats_repo(config)
    return FsrsStatsService(repo=repo, calculator=MetricsCalculator())
