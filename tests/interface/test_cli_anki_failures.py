"""A failed read of Anki is an error the user sees, never an empty answer.

An empty result (no stats, no cards, "Have you synced?") would pass for Anki's answer, so
a failure prints its reason on stderr and the exit status is 1.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arete.application.queue.service import build_study_queue, create_queue_deck
from arete.interface.cli import app
from tests.interface.test_cli_anki_args import BACKEND, _bridge, runner


def test_stats_that_fail_to_load_say_why_on_stderr_and_exit_1():
    repo = MagicMock()
    repo.get_card_stats = AsyncMock(side_effect=RuntimeError("AnkiConnect error: busy"))
    with patch("arete.composition.factory.get_stats_repo", return_value=repo):
        result = runner.invoke(app, ["anki", "stats", "--nids", "[1]", *BACKEND])

    assert result.exit_code == 1
    assert "AnkiConnect error: busy" in result.stderr
    assert result.stdout.strip() == "", "no empty JSON list"


def test_a_queue_deck_whose_card_lookup_fails_says_why_and_exits_1():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls)
        inst.get_card_ids_for_arete_ids = AsyncMock(
            side_effect=RuntimeError("looking up the cards of arete_a failed: locked")
        )
        result = runner.invoke(app, ["anki", "queue-deck", "--ids", "arete_a", *BACKEND])

    assert result.exit_code == 1
    assert "arete_a failed: locked" in result.stderr
    assert "Have you synced?" not in result.output


@pytest.mark.asyncio
async def test_the_queue_raises_when_the_due_cards_cannot_be_mapped(tmp_path):
    anki = MagicMock()
    anki.get_due_cards = AsyncMock(return_value=[1, 2])
    anki.map_nids_to_arete_ids = AsyncMock(side_effect=RuntimeError("notesInfo failed"))

    with pytest.raises(RuntimeError, match="notesInfo failed"):
        await build_study_queue(anki, tmp_path)


@pytest.mark.asyncio
async def test_a_queue_deck_raises_when_its_cards_cannot_be_looked_up():
    anki = MagicMock()
    anki.get_card_ids_for_arete_ids = AsyncMock(side_effect=RuntimeError("locked"))

    with pytest.raises(RuntimeError, match="locked"):
        await create_queue_deck(anki, ["arete_a"], deck_name="Q")
