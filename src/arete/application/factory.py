"""Composition Root
Centralizes construction of concrete infrastructure implementations.
This is the ONLY application-layer module allowed to import from infrastructure.
"""

from pathlib import Path

from arete.application.config import AppConfig
from arete.application.sync.vault_service import VaultService
from arete.domain.interfaces import AnkiBridge, ContentCache
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from arete.infrastructure.adapters.stats import ConnectStatsRepository, DirectStatsRepository
from arete.infrastructure.persistence.cache import ContentCache as _CacheImpl


async def get_anki_bridge(config: AppConfig) -> AnkiBridge:
    """Returns the appropriate AnkiBridge implementation based on config and responsiveness."""
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
    """Returns a ContentCache implementation."""
    return _CacheImpl(db_path=db_path)


def get_vault_service(config: AppConfig) -> VaultService:
    """Returns the VaultService instance configured for the given app config."""
    if config.vault_root is None:
        raise ValueError("vault_root is required for VaultService")
    cache = _CacheImpl(config.vault_root / ".arete.db")
    return VaultService(config.vault_root, cache, ignore_cache=config.clear_cache)


def get_stats_repo(config: AppConfig) -> StatsRepository:
    """Returns the appropriate StatsRepository implementation based on config."""
    if config.backend == "ankiconnect":
        url = config.anki_connect_url or "http://localhost:8765"
        return ConnectStatsRepository(url=url)
    return DirectStatsRepository(anki_base=config.anki_base)
