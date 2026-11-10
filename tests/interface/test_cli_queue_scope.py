"""`arete queue` puts prerequisites first, and a named deck is an isolated set.

The vault and graph are real; Anki is a fake bridge that reports one due card.
"""

from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from arete.interface.cli import app as cli_app
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
    "no deck": ([], ["arete_A", "arete_B"]),
    "named deck": (["--deck", "Math"], ["arete_B"]),
    "named deck across decks": (["--deck", "Math", "--cross-deck"], ["arete_A", "arete_B"]),
}


@pytest.mark.parametrize("name", list(CASES))
def test_queue_order_for_each_deck_scope(configured_vault, name):
    write_note(
        configured_vault,
        "Algebra.md",
        deck="Math",
        cards=[
            card(arete_id="arete_A", front="a"),
            card(arete_id="arete_B", front="b", requires=["arete_A"]),
        ],
    )
    flags, expected = CASES[name]

    bridge = FakeBridge(["arete_B"])
    with patch("arete.composition.factory.get_anki_bridge", AsyncMock(return_value=bridge)):
        ran = CliRunner().invoke(cli_app, ["queue", str(configured_vault), *flags])

    assert ran.exit_code == 0
    assert bridge.resolved[0] == expected
