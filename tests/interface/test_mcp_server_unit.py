"""Unit tests for the Arete MCP server (FastMCP-based)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.interface.mcp_server import _find_concept_file, create_server


@pytest.fixture
def mcp_server():
    """Create a fresh MCP server instance for testing."""
    return create_server()


def _text(result: tuple) -> str:
    """Extract text from FastMCP call_tool result (tuple of (list[TextContent], dict))."""
    return result[0][0].text


# ------------------------------------------------------------------
# sync_vault
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_vault_success(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.execute_sync", new_callable=AsyncMock) as mock_exec,
    ):
        mock_exec.return_value.total_errors = 0
        mock_exec.return_value.total_imported = 10
        mock_exec.return_value.total_generated = 5

        tools = {t.name: t for t in await mcp_server.list_tools()}
        assert "sync_vault" in tools

        result = await mcp_server.call_tool("sync_vault", {"force": True})
        data = json.loads(_text(result))
        assert data["success"] is True
        assert data["total_imported"] == 10


# ------------------------------------------------------------------
# sync_file
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_file_success(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.execute_sync", new_callable=AsyncMock) as mock_exec,
        patch("arete.interface.mcp_server.Path") as mock_path_cls,
    ):
        mock_path_cls.return_value.exists.return_value = True
        mock_path_cls.return_value.__str__ = lambda self: "test.md"

        mock_exec.return_value.total_errors = 0
        mock_exec.return_value.total_imported = 1

        result = await mcp_server.call_tool("sync_file", {"file_path": "test.md"})
        data = json.loads(_text(result))
        assert data["success"] is True


@pytest.mark.asyncio
async def test_sync_file_not_found(mcp_server):
    with patch("arete.interface.mcp_server.Path") as mock_path_cls:
        mock_path_cls.return_value.exists.return_value = False
        result = await mcp_server.call_tool("sync_file", {"file_path": "missing.md"})
        assert "File not found" in _text(result)


# ------------------------------------------------------------------
# get_stats
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stats(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock),
        patch(
            "arete.application.stats.learning_insights_service.LearningInsightsService.get_learning_insights",
            new_callable=AsyncMock,
        ) as mock_insights,
    ):
        from dataclasses import dataclass

        @dataclass
        class FakeInsights:
            total_cards: int = 100
            leeches: int = 5

        mock_insights.return_value = FakeInsights()

        result = await mcp_server.call_tool("get_stats", {"lapse_threshold": 3})
        data = json.loads(_text(result))
        assert data["total_cards"] == 100


# ------------------------------------------------------------------
# browse_concept
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_concept(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock) as mock_get,
    ):
        mock_bridge = AsyncMock()
        mock_bridge.gui_browse.return_value = True
        mock_get.return_value = mock_bridge

        result = await mcp_server.call_tool(
            "browse_concept", {"concept": "Hash Table", "deck": "CS::DSA"}
        )
        assert "Opened Anki browser" in _text(result)
        mock_bridge.gui_browse.assert_awaited_once()


@pytest.mark.asyncio
async def test_browse_concept_failure(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock) as mock_get,
    ):
        mock_bridge = AsyncMock()
        mock_bridge.gui_browse.return_value = False
        mock_get.return_value = mock_bridge

        result = await mcp_server.call_tool(
            "browse_concept", {"concept": "Hash Table"}
        )
        assert "Failed" in _text(result)


# ------------------------------------------------------------------
# browse_card
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_card(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock) as mock_get,
    ):
        mock_bridge = AsyncMock()
        mock_bridge.gui_browse.return_value = True
        mock_get.return_value = mock_bridge

        result = await mcp_server.call_tool(
            "browse_card", {"arete_id": "arete_01ARZ3NDEKTSV4RRFFQ69G5FAV"}
        )
        assert "Opened Anki browser" in _text(result)
        call_args = mock_bridge.gui_browse.call_args[0][0]
        assert "arete_01ARZ3NDEKTSV4RRFFQ69G5FAV" in call_args


# ------------------------------------------------------------------
# get_concept_cards
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_concept_cards(mcp_server, tmp_path):
    concept_file = tmp_path / "Hash Table.md"
    concept_file.write_text(
        """---
arete: true
deck: "CS::DSA"
cards:
  - id: arete_001
    model: Basic
    Front: "What is a hash table?"
    Back: "A data structure mapping keys to values."
  - id: arete_002
    model: Basic
    Front: "Hash table time complexity?"
    Back: "O(1) average for lookup."
---
Body text here.
"""
    )

    mock_config = MagicMock()
    mock_config.vault_root = tmp_path

    with patch("arete.interface.mcp_server.resolve_config", return_value=mock_config):
        result = await mcp_server.call_tool(
            "get_concept_cards", {"concept": "Hash Table"}
        )
        data = json.loads(_text(result))
        assert data["card_count"] == 2
        assert data["cards"][0]["Front"] == "What is a hash table?"
        assert data["cards"][0]["arete_id"] == "arete_001"


@pytest.mark.asyncio
async def test_get_concept_cards_not_found(mcp_server, tmp_path):
    mock_config = MagicMock()
    mock_config.vault_root = tmp_path

    with patch("arete.interface.mcp_server.resolve_config", return_value=mock_config):
        result = await mcp_server.call_tool(
            "get_concept_cards", {"concept": "Nonexistent"}
        )
        assert "No vault note found" in _text(result)


# ------------------------------------------------------------------
# get_due_cards
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_due_cards(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock) as mock_get,
    ):
        mock_bridge = AsyncMock()
        mock_bridge.get_due_cards.return_value = [1001, 1002, 1003]
        mock_bridge.map_nids_to_arete_ids.return_value = ["arete_a", "arete_b", "arete_c"]
        mock_get.return_value = mock_bridge

        result = await mcp_server.call_tool(
            "get_due_cards", {"deck": "CS::DSA", "include_new": False}
        )
        data = json.loads(_text(result))
        assert data["due_count"] == 3
        assert len(data["arete_ids"]) == 3


@pytest.mark.asyncio
async def test_get_due_cards_none(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock) as mock_get,
    ):
        mock_bridge = AsyncMock()
        mock_bridge.get_due_cards.return_value = []
        mock_get.return_value = mock_bridge

        result = await mcp_server.call_tool(
            "get_due_cards", {"deck": "CS::DSA"}
        )
        assert "No cards due" in _text(result)


# ------------------------------------------------------------------
# _find_concept_file
# ------------------------------------------------------------------


def test_find_concept_file_exact(tmp_path):
    (tmp_path / "Hash Table.md").touch()
    result = _find_concept_file(tmp_path, "Hash Table")
    assert result is not None
    assert result.name == "Hash Table.md"


def test_find_concept_file_case_insensitive(tmp_path):
    (tmp_path / "hash table.md").touch()
    result = _find_concept_file(tmp_path, "Hash Table")
    assert result is not None


def test_find_concept_file_subdirectory(tmp_path):
    subdir = tmp_path / "concepts"
    subdir.mkdir()
    (subdir / "Binary Search.md").touch()
    result = _find_concept_file(tmp_path, "Binary Search")
    assert result is not None
    assert result.name == "Binary Search.md"


def test_find_concept_file_not_found(tmp_path):
    result = _find_concept_file(tmp_path, "Nonexistent")
    assert result is None
