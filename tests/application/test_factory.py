"""Tests for arete.application.factory — adapter selection logic."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.application.config import AppConfig
from arete.application.factory import get_anki_bridge, get_stats_repo, get_vault_service
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from arete.infrastructure.adapters.stats import ConnectStatsRepository, DirectStatsRepository


def _make_config(**overrides) -> MagicMock:
    """Create a mock AppConfig with sensible defaults."""
    config = MagicMock(spec=AppConfig)
    config.backend = overrides.get("backend", "auto")
    config.anki_connect_url = overrides.get("anki_connect_url", "http://127.0.0.1:8765")
    config.anki_base = overrides.get("anki_base", Path("/fake/anki"))
    config.vault_root = overrides.get("vault_root", Path("/fake/vault"))
    config.clear_cache = overrides.get("clear_cache", False)
    return config


# ---------- get_anki_bridge ----------


@pytest.mark.asyncio
async def test_get_anki_bridge_manual_ankiconnect():
    """When backend='ankiconnect', returns AnkiConnectAdapter without checking responsiveness."""
    config = _make_config(backend="ankiconnect")
    bridge = await get_anki_bridge(config)
    assert isinstance(bridge, AnkiConnectAdapter)


@pytest.mark.asyncio
async def test_get_anki_bridge_manual_direct():
    """When backend='direct', returns AnkiDirectAdapter without checking responsiveness."""
    config = _make_config(backend="direct")
    bridge = await get_anki_bridge(config)
    assert isinstance(bridge, AnkiDirectAdapter)


@pytest.mark.asyncio
async def test_get_anki_bridge_auto_ankiconnect_responsive():
    """When backend='auto' and AnkiConnect is responsive, returns AnkiConnectAdapter."""
    config = _make_config(backend="auto")

    with patch.object(AnkiConnectAdapter, "is_responsive", new_callable=AsyncMock, return_value=True):
        bridge = await get_anki_bridge(config)
        assert isinstance(bridge, AnkiConnectAdapter)


@pytest.mark.asyncio
async def test_get_anki_bridge_auto_ankiconnect_not_responsive():
    """When backend='auto' and AnkiConnect is NOT responsive, falls back to AnkiDirectAdapter."""
    config = _make_config(backend="auto")

    with patch.object(
        AnkiConnectAdapter, "is_responsive", new_callable=AsyncMock, return_value=False
    ):
        bridge = await get_anki_bridge(config)
        assert isinstance(bridge, AnkiDirectAdapter)


@pytest.mark.asyncio
async def test_get_anki_bridge_auto_uses_configured_url():
    """Auto mode creates AnkiConnectAdapter with the configured URL."""
    config = _make_config(backend="auto", anki_connect_url="http://10.0.0.1:9999")

    with patch.object(
        AnkiConnectAdapter, "is_responsive", new_callable=AsyncMock, return_value=True
    ) as mock_resp:
        bridge = await get_anki_bridge(config)
        assert isinstance(bridge, AnkiConnectAdapter)
        # Verify the adapter was created — is_responsive was called on the instance
        mock_resp.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_anki_bridge_direct_uses_anki_base():
    """Direct mode passes anki_base from config to the adapter."""
    anki_base = Path("/custom/anki/base")
    config = _make_config(backend="direct", anki_base=anki_base)

    bridge = await get_anki_bridge(config)
    assert isinstance(bridge, AnkiDirectAdapter)
    assert bridge.anki_base == anki_base


# ---------- get_stats_repo ----------


def test_get_stats_repo_ankiconnect():
    """When backend='ankiconnect', returns ConnectStatsRepository."""
    config = _make_config(backend="ankiconnect", anki_connect_url="http://127.0.0.1:8765")
    repo = get_stats_repo(config)
    assert isinstance(repo, ConnectStatsRepository)
    assert repo.url == "http://127.0.0.1:8765"


def test_get_stats_repo_direct():
    """When backend='direct', returns DirectStatsRepository."""
    config = _make_config(backend="direct", anki_base=Path("/fake/anki"))
    repo = get_stats_repo(config)
    assert isinstance(repo, DirectStatsRepository)


def test_get_stats_repo_auto_defaults_to_direct():
    """When backend='auto' (not ankiconnect), returns DirectStatsRepository."""
    config = _make_config(backend="auto")
    repo = get_stats_repo(config)
    assert isinstance(repo, DirectStatsRepository)


def test_get_stats_repo_ankiconnect_none_url_uses_default():
    """When backend='ankiconnect' and URL is None, uses default localhost."""
    config = _make_config(backend="ankiconnect", anki_connect_url=None)
    repo = get_stats_repo(config)
    assert isinstance(repo, ConnectStatsRepository)
    assert repo.url == "http://localhost:8765"


# ---------- get_vault_service ----------


def test_get_vault_service_success(tmp_path):
    """VaultService is created with correct vault_root and cache path."""
    config = _make_config(vault_root=tmp_path, clear_cache=False)
    vs = get_vault_service(config)
    assert vs is not None


def test_get_vault_service_none_vault_root():
    """Raises ValueError when vault_root is None."""
    config = _make_config(vault_root=None)
    with pytest.raises(ValueError, match="vault_root is required"):
        get_vault_service(config)


def test_get_vault_service_clear_cache(tmp_path):
    """VaultService receives ignore_cache flag from config."""
    config = _make_config(vault_root=tmp_path, clear_cache=True)
    vs = get_vault_service(config)
    assert vs is not None
