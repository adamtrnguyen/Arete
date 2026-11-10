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
    for name, value in methods.items():
        setattr(inst, name, AsyncMock(return_value=value))
    return inst


# --- stats ---


def test_stats_invalid_json_nids_exits_1():
    result = runner.invoke(app, ["anki", "stats", "--nids", "[1, 2", *BACKEND])
    assert result.exit_code == 1
    assert "Invalid JSON for --nids" in result.stdout


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
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": False}
    inst.unsuspend_cards.assert_awaited_once_with([5])


# --- browse ---


def test_browse_without_query_or_nid_exits_1_and_never_reaches_anki():
    with patch("arete.composition.factory.AnkiConnectAdapter") as mock_cls:
        inst = _bridge(mock_cls, gui_browse=True)
        result = runner.invoke(app, ["anki", "browse", *BACKEND])
    assert result.exit_code == 1
    assert "Must specify --query or --nid" in result.stdout
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
