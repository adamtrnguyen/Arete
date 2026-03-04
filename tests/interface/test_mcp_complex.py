"""Integration-style tests for the Arete MCP server (FastMCP-based).

These tests verify tool registration, dispatch, and error handling at the
FastMCP server level.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.interface.mcp_server import create_server


@pytest.fixture
def mcp_server():
    """Create a fresh MCP server instance for testing."""
    return create_server()


def _text(result: tuple) -> str:
    """Extract text from FastMCP call_tool result."""
    return result[0][0].text


@pytest.mark.asyncio
async def test_get_stats_success(mcp_server):
    from arete.domain.stats.models import LearningStats

    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.get_anki_bridge", new_callable=AsyncMock),
        patch("arete.application.stats.learning_insights_service.LearningInsightsService") as MockService,
    ):
        mock_service_instance = MockService.return_value
        mock_insights = LearningStats(total_cards=100)
        mock_service_instance.get_learning_insights = AsyncMock(return_value=mock_insights)

        result = await mcp_server.call_tool("get_stats", {"lapse_threshold": 5})
        text = _text(result)
        assert "total_cards" in text
        mock_service_instance.get_learning_insights.assert_awaited_with(lapse_threshold=5)


@pytest.mark.asyncio
async def test_mcp_tool_listing(mcp_server):
    """Verify all expected tools are registered."""
    tools = await mcp_server.list_tools()
    tool_names = {t.name for t in tools}

    expected = {
        "sync_vault",
        "sync_file",
        "get_stats",
        "browse_concept",
        "browse_card",
        "get_concept_cards",
        "get_due_cards",
        "build_study_queue",
    }
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


@pytest.mark.asyncio
async def test_sync_vault_with_all_args(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.execute_sync", new_callable=AsyncMock) as mock_exec,
    ):
        mock_stats = MagicMock()
        mock_stats.total_generated = 0
        mock_stats.total_imported = 0
        mock_stats.total_errors = 0
        mock_exec.return_value = mock_stats

        result = await mcp_server.call_tool(
            "sync_vault", {"vault_path": "/tmp/v", "force": True, "prune": True}
        )
        data = json.loads(_text(result))
        assert data["success"] is True


@pytest.mark.asyncio
async def test_sync_file_with_force(mcp_server):
    with (
        patch("arete.interface.mcp_server.resolve_config"),
        patch("arete.interface.mcp_server.Path") as mock_path,
        patch("arete.interface.mcp_server.execute_sync", new_callable=AsyncMock) as mock_exec,
    ):
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.__str__ = lambda self: "f.md"

        mock_stats = MagicMock()
        mock_stats.total_generated = 0
        mock_stats.total_imported = 0
        mock_stats.total_errors = 0
        mock_exec.return_value = mock_stats

        result = await mcp_server.call_tool(
            "sync_file", {"file_path": "f.md", "force": True}
        )
        data = json.loads(_text(result))
        assert data["success"] is True
