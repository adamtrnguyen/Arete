"""The CLI and the MCP tool return the same queue for the same vault.

Both delegate to `application/queue/service.py`; the vault and graph are real, Anki is a
fake bridge.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent
from typer.testing import CliRunner

from arete.interface.cli import app as cli_app
from arete.interface.mcp_server import create_server
from tests.factories import card, write_note


class FakeBridge:
    """Every due note maps to the given Arete ids; the cards sent to the filtered deck are kept."""

    def __init__(self, arete_ids: list[str]):
        """`arete_ids` are the ids behind the due notes."""
        self.arete_ids = arete_ids
        self.resolved: list[list[str]] = []

    async def get_due_cards(self, deck_name=None, include_new=False):
        return list(range(1, len(self.arete_ids) + 1))

    async def map_nids_to_arete_ids(self, nids):
        return list(self.arete_ids)

    async def get_card_ids_for_arete_ids(self, arete_ids):
        self.resolved.append(list(arete_ids))
        return [100 + i for i, _ in enumerate(arete_ids)]

    async def create_topo_deck(self, deck_name, cids, reschedule=True):
        return True

    async def close(self):
        return None


CASES = {
    "no deck": ({}, []),
    "named deck": ({"deck": "Math"}, ["--deck", "Math"]),
    "named deck across decks": (
        {"deck": "Math", "cross_deck": True},
        ["--deck", "Math", "--cross-deck"],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_cli_and_mcp_queue_the_same_cards(configured_vault, name):
    write_note(
        configured_vault,
        "Algebra.md",
        deck="Math",
        cards=[
            card(arete_id="arete_A", front="a"),
            card(arete_id="arete_B", front="b", requires=["arete_A"]),
        ],
    )
    args, cli_flags = CASES[name]

    mcp_bridge = FakeBridge(["arete_B"])
    with patch("arete.interface.mcp_server.get_anki_bridge", AsyncMock(return_value=mcp_bridge)):
        result = asyncio.run(create_server().call_tool("build_study_queue", args))
    content = result.content[0]
    assert isinstance(content, TextContent)
    mcp_order = json.loads(content.text)["queue_order"]

    cli_bridge = FakeBridge(["arete_B"])
    with patch("arete.composition.factory.get_anki_bridge", AsyncMock(return_value=cli_bridge)):
        ran = CliRunner().invoke(cli_app, ["queue", str(configured_vault), *cli_flags])
    assert ran.exit_code == 0
    cli_order = cli_bridge.resolved[0]

    assert mcp_order == cli_order
    assert mcp_order == (["arete_A", "arete_B"] if name != "named deck" else ["arete_B"])
