from unittest.mock import MagicMock

import pytest

from arete.application.sync.vault_service import VaultService
from arete.infrastructure.persistence.cache import ContentCache


@pytest.fixture
def mock_cache():
    # Helper to mock ContentCache
    c = MagicMock(spec=ContentCache)
    c.get_file_meta_by_stat.return_value = None
    c.set_file_meta.return_value = None
    return c


def test_quick_check_valid_file(tmp_path, mock_cache):
    f = tmp_path / "valid.md"
    f.write_text(
        """---
deck: Test
cards:
  - Front: Q
    Back: A
---
Content
""",
        encoding="utf-8",
    )

    service = VaultService(root=tmp_path, cache=mock_cache)
    ok, count, err, meta, fresh = service._quick_check_file(f)
    assert ok is True
    assert count == 1
    assert err is None
    assert meta is not None
    assert meta["deck"] == "Test"
    assert fresh is True  # New file = fresh


def test_quick_check_no_cards(tmp_path, mock_cache):
    f = tmp_path / "empty.md"
    f.write_text(
        """---
deck: Test
cards: []
---
""",
        encoding="utf-8",
    )

    service = VaultService(root=tmp_path, cache=mock_cache)
    ok, count, err, meta, fresh = service._quick_check_file(f)
    assert ok is False
    assert err == "no_cards"


def test_quick_check_cache_hit(tmp_path, mock_cache):
    f = tmp_path / "cached.md"
    f.write_text("Dummy content on disk", encoding="utf-8")

    # Simulate cache hit: logic uses get_file_meta_by_stat now
    mock_cache.get_file_meta_by_stat.return_value = {"cards": [1, 2, 3], "deck": "D"}

    service = VaultService(root=tmp_path, cache=mock_cache)
    ok, count, err, meta, fresh = service._quick_check_file(f)

    assert ok is True
    assert count == 3
    assert fresh is False  # Cache hit = not fresh
    # Should not have parsed the "Dummy content" as YAML because it hit cache
