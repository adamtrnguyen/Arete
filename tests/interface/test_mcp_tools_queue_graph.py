"""MCP tools that read the vault graph or build a queue.

Covers build_study_queue, check_graph, get_dep_subgraph, list_file_cards and browse_card.

The vault is real (the `configured_vault` fixture); Anki is a fake bridge.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from arete.interface.mcp_server import create_server
from tests.factories import card, write_note


def _data(result) -> dict:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


def _text(result) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


class FakeBridge:
    """Queue-capable bridge: every due note maps to the given Arete ids."""

    def __init__(self, due: int, arete_ids: list[str], deck_ok: bool = True):
        """`due` notes are due; `arete_ids` are the ones carrying an Arete id tag."""
        self.due = due
        self.arete_ids = arete_ids
        self.deck_ok = deck_ok
        self.due_args: list[tuple] = []
        self.created: list[tuple[str, list[int], bool]] = []

    async def get_due_cards(self, deck_name=None, include_new=False):
        self.due_args.append((deck_name, include_new))
        return list(range(1, self.due + 1))

    async def map_nids_to_arete_ids(self, nids):
        return list(self.arete_ids)

    async def get_card_ids_for_arete_ids(self, arete_ids):
        return [100 + i for i, _ in enumerate(arete_ids)]

    async def create_topo_deck(self, deck_name, cids, reschedule=True):
        self.created.append((deck_name, list(cids), reschedule))
        return self.deck_ok


@pytest.fixture
def algebra(configured_vault):
    write_note(
        configured_vault,
        "Algebra.md",
        deck="Math",
        cards=[
            card(arete_id="arete_A", front="a"),
            card(arete_id="arete_B", front="b", requires=["arete_A"]),
        ],
    )
    return configured_vault


# --- build_study_queue ---


@pytest.mark.asyncio
async def test_build_study_queue_with_nothing_due_says_so(algebra):
    bridge = FakeBridge(0, [])
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=bridge)):
        result = await create_server().call_tool("build_study_queue", {})
    assert _text(result) == "No cards due in deck CS::DSA"
    assert bridge.created == []


@pytest.mark.asyncio
async def test_build_study_queue_without_a_deck_orders_prerequisites_first(algebra):
    bridge = FakeBridge(1, ["arete_B"])
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=bridge)):
        result = await create_server().call_tool(
            "build_study_queue", {"deck": "", "depth": 2, "include_new": True}
        )
    data = _data(result)
    assert data["queue_order"] == ["arete_A", "arete_B"]
    assert data["due_cards"] == 1
    assert data["prereq_cards"] == 1
    assert data["total_queued"] == 2
    assert data["missing_prereqs"] == []
    assert data["cycles"] == []
    assert bridge.due_args == [("", True)]
    assert bridge.created == [("Arete::Queue", [100, 101], True)]


@pytest.mark.xfail(
    strict=True,
    reason="build_study_queue always has a deck (default CS::DSA) and has no cross_deck "
    "argument, so depth is ignored and prerequisites are never queued",
)
@pytest.mark.asyncio
async def test_build_study_queue_includes_prerequisites_for_a_named_deck(algebra):
    bridge = FakeBridge(1, ["arete_B"])
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=bridge)):
        result = await create_server().call_tool("build_study_queue", {"deck": "Math", "depth": 2})
    assert _data(result)["queue_order"] == ["arete_A", "arete_B"]


@pytest.mark.asyncio
async def test_build_study_queue_for_a_named_deck_is_an_isolated_set(algebra):
    bridge = FakeBridge(1, ["arete_B"])
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=bridge)):
        result = await create_server().call_tool(
            "build_study_queue", {"deck": "Math", "include_new": True}
        )
    data = _data(result)
    assert data["deck"] == "Math"
    assert data["queue_order"] == ["arete_B"]
    assert bridge.due_args == [("Math", True)]
    assert bridge.created == [("Arete::Queue", [100], True)]


@pytest.mark.asyncio
async def test_build_study_queue_lists_prerequisites_that_do_not_exist(configured_vault):
    write_note(configured_vault, cards=[card(arete_id="arete_A", requires=["arete_gone"])])
    bridge = FakeBridge(1, ["arete_A"])
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=bridge)):
        result = await create_server().call_tool("build_study_queue", {"algo": "dynamic"})
    assert _data(result)["missing_prereqs"] == ["arete_gone"]


# --- browse_card ---


@pytest.mark.asyncio
async def test_browse_card_says_so_when_anki_refuses():
    bridge = AsyncMock()
    bridge.gui_browse.return_value = False
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=bridge)):
        result = await create_server().call_tool("browse_card", {"arete_id": "arete_A"})
    assert _text(result) == "Failed to open Anki browser. Is Anki running with AnkiConnect?"
    bridge.gui_browse.assert_awaited_once_with("tag:arete_A")


# --- check_graph ---


@pytest.mark.asyncio
async def test_check_graph_reports_the_vault_graph(algebra):
    data = _data(await create_server().call_tool("check_graph", {}))
    assert data["ok"] is True
    assert data["total_nodes"] == 2
    assert data["total_edges"] == 1


@pytest.mark.asyncio
async def test_check_graph_deck_filter_restricts_the_report(algebra):
    write_note(
        algebra,
        "Art.md",
        deck="Art",
        cards=[card(arete_id="arete_X", front="x"), card(arete_id="arete_Y", front="y")],
    )
    data = _data(await create_server().call_tool("check_graph", {"deck": "Art"}))
    assert data["deck_filter"] == "Art"
    assert data["total_nodes"] == 2


@pytest.mark.asyncio
async def test_check_graph_refuses_a_folder_outside_the_vault(algebra, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ToolError, match="outside the configured vault"):
        await create_server().call_tool("check_graph", {"vault_path": str(outside)})


# --- get_dep_subgraph ---


@pytest.mark.asyncio
async def test_dep_subgraph_returns_the_batch_and_its_outside_prerequisites(algebra):
    write_note(
        algebra,
        "Calc.md",
        deck="Math",
        cards=[card(arete_id="arete_C", front="c", requires=["arete_A"])],
    )
    batch = str(algebra / "Calc.md")
    data = _data(await create_server().call_tool("get_dep_subgraph", {"file_paths": batch}))
    assert data["batch_cards"] == 1
    assert data["external_deps"] == 1


@pytest.mark.asyncio
async def test_dep_subgraph_of_a_file_with_no_cards_is_empty(algebra):
    data = _data(
        await create_server().call_tool(
            "get_dep_subgraph", {"file_paths": str(algebra / "Nope.md") + " , "}
        )
    )
    assert data["batch_cards"] == 0
    assert data["nodes"] == []


# --- list_file_cards ---


@pytest.mark.asyncio
async def test_list_file_cards_returns_each_card_with_its_content(algebra):
    data = _data(await create_server().call_tool("list_file_cards", {"file_path": "Algebra.md"}))
    ids = [c["id"] for c in data["cards"]]
    assert ids == ["arete_A", "arete_B"]


@pytest.mark.asyncio
async def test_list_file_cards_reports_a_missing_file_as_an_error_field(algebra):
    data = _data(await create_server().call_tool("list_file_cards", {"file_path": "Missing.md"}))
    assert "File not found" in data["error"]
