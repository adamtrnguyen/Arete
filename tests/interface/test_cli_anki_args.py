"""Argument handling of `arete anki ...` and the exit code of a failed `arete sync`."""

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from arete.composition.orchestrator import SyncFailedError
from arete.interface.cli import app

runner = CliRunner()

BACKEND = ["--backend", "ankiconnect", "--anki-connect-url", "http://fake"]


def _bridge(mock_cls, **methods):
    inst = mock_cls.return_value
    inst.is_responsive = AsyncMock(return_value=True)
    inst.close = AsyncMock()
    for name, value in methods.items():
        setattr(inst, name, AsyncMock(return_value=value))
    return inst


# --- stats ---


def test_stats_invalid_json_nids_exits_1():
    result = runner.invoke(app, ["anki", "stats", "--nids", "[1, 2", *BACKEND])
    assert result.exit_code == 1
    assert "Invalid JSON for --nids" in result.stderr
    assert result.stdout == ""


def test_stats_with_no_valid_nids_prints_an_empty_json_list():
    result = runner.invoke(app, ["anki", "stats", "--nids", "abc,,x", *BACKEND])
    assert result.exit_code == 0
    assert result.stdout.strip() == "[]"


def test_stats_with_no_valid_nids_in_table_mode_says_so():
    result = runner.invoke(app, ["anki", "stats", "--nids", "[]", "--no-json", *BACKEND])
    assert result.exit_code == 0
    assert "No valid NIDs provided." in result.stdout


def test_stats_table_shows_a_dash_for_a_card_without_fsrs_difficulty():
    from arete.domain.stats.models import CardStatsAggregate

    stat = CardStatsAggregate(
        card_id=7,
        note_id=1,
        deck_name="Default",
        lapses=0,
        ease=2500,
        interval=1,
        due=1,
        reps=0,
        fsrs=None,
        last_review=None,
    )
    repo = MagicMock()
    repo.get_card_stats = AsyncMock(return_value=[stat])
    repo.get_review_history = AsyncMock(return_value=[])
    repo.get_deck_params = AsyncMock(return_value={})
    with patch("arete.composition.factory.get_stats_repo", return_value=repo):
        result = runner.invoke(app, ["anki", "stats", "--nids", "[1]", "--no-json", *BACKEND])
    assert result.exit_code == 0
    repo.get_card_stats.assert_awaited_once_with([1])
    row = next(line for line in result.stdout.splitlines() if "Default" in line)
    assert re.search(r"Default\s*│\s*-\s*│", row), row


# --- suspend / unsuspend: how card ids are parsed ---


def test_suspend_accepts_a_json_list_of_card_ids():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, suspend_cards=True)
        result = runner.invoke(app, ["anki", "suspend", "--cids", "[11, 22]", *BACKEND])
    assert result.exit_code == 0
    inst.suspend_cards.assert_awaited_once_with([11, 22])


def test_suspend_accepts_comma_separated_card_ids_with_spaces():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, suspend_cards=True)
        result = runner.invoke(app, ["anki", "suspend", "--cids", "11, 22 ,33", *BACKEND])
    assert result.exit_code == 0
    inst.suspend_cards.assert_awaited_once_with([11, 22, 33])


def test_unsuspend_prints_what_the_bridge_returned():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, unsuspend_cards=False)
        result = runner.invoke(app, ["anki", "unsuspend", "--cids", "5", *BACKEND])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": False}
    inst.unsuspend_cards.assert_awaited_once_with([5])
    inst.close.assert_awaited_once()


# --- decks ---


def test_decks_prints_only_the_deck_names_as_json_and_closes_the_bridge():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, get_deck_names=["Default", "Math::Algebra"])
        result = runner.invoke(app, ["anki", "decks", *BACKEND])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"decks": ["Default", "Math::Algebra"]}
    inst.close.assert_awaited_once()


def test_decks_moves_what_a_library_prints_to_stderr():
    async def noisy():
        print("all_names is deprecated")
        return ["Default"]

    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls)
        inst.get_deck_names = noisy
        result = runner.invoke(app, ["anki", "decks", *BACKEND])
    assert json.loads(result.stdout) == {"decks": ["Default"]}
    assert "all_names is deprecated" in result.stderr


# --- queue-deck ---


def test_queue_deck_sends_the_cards_in_the_given_order():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, get_card_ids_for_arete_ids=[7, 0, 9], create_topo_deck=True)
        ids = json.dumps(["arete_B", "arete_X", "arete_A"])
        result = runner.invoke(app, ["anki", "queue-deck", "--ids", ids, *BACKEND])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "message": "Created deck 'Arete Study Queue' with 2 cards.",
    }
    inst.get_card_ids_for_arete_ids.assert_awaited_once_with(["arete_B", "arete_X", "arete_A"])
    inst.create_topo_deck.assert_awaited_once_with("Arete Study Queue", [7, 9], reschedule=True)
    inst.close.assert_awaited_once()


def test_queue_deck_takes_a_deck_name_reschedule_and_comma_separated_ids():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, get_card_ids_for_arete_ids=[7], create_topo_deck=True)
        args = ["--ids", "arete_A, arete_B", "--deck-name", "Q", "--no-reschedule"]
        result = runner.invoke(app, ["anki", "queue-deck", *args, *BACKEND])
    assert result.exit_code == 0
    inst.get_card_ids_for_arete_ids.assert_awaited_once_with(["arete_A", "arete_B"])
    inst.create_topo_deck.assert_awaited_once_with("Q", [7], reschedule=False)


def test_queue_deck_with_no_synced_card_makes_no_deck():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, get_card_ids_for_arete_ids=[], create_topo_deck=True)
        result = runner.invoke(app, ["anki", "queue-deck", "--ids", '["arete_A"]', *BACKEND])
    assert json.loads(result.stdout) == {
        "ok": False,
        "message": "No matching Anki cards found. Have you synced?",
    }
    inst.create_topo_deck.assert_not_awaited()


def test_queue_deck_reports_a_failed_deck():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        _bridge(mock_cls, get_card_ids_for_arete_ids=[7], create_topo_deck=False)
        result = runner.invoke(app, ["anki", "queue-deck", "--ids", '["arete_A"]', *BACKEND])
    assert json.loads(result.stdout) == {
        "ok": False,
        "message": "Failed to create deck (check logs).",
    }


# --- browse ---


def test_browse_without_query_or_nid_exits_1_and_never_reaches_anki():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, gui_browse=True)
        result = runner.invoke(app, ["anki", "browse", *BACKEND])
    assert result.exit_code == 1
    assert "Must specify --query or --nid" in result.stderr
    assert result.stdout == ""
    inst.gui_browse.assert_not_awaited()


def test_browse_nid_becomes_an_nid_search():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, gui_browse=True)
        result = runner.invoke(app, ["anki", "browse", "--nid", "123", *BACKEND])
    assert result.exit_code == 0
    inst.gui_browse.assert_awaited_once_with("nid:123")


def test_browse_query_wins_over_nid():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, gui_browse=True)
        runner.invoke(app, ["anki", "browse", "--query", "deck:X", "--nid", "123", *BACKEND])
    inst.gui_browse.assert_awaited_once_with("deck:X")


# --- sync ---


def test_sync_that_fails_exits_1():
    failing = AsyncMock(side_effect=SyncFailedError("3 errors during sync"))
    with patch("arete.composition.orchestrator.run_sync_logic", failing):
        result = runner.invoke(app, ["sync", "/tmp/arete-vault-not-read"])
    assert result.exit_code == 1
    failing.assert_awaited_once()
