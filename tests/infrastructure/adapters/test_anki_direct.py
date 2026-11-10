"""Consolidated unit tests for AnkiDirectAdapter.

Every public method of AnkiDirectAdapter is covered here, organized by
method name.  Both success and failure / no-collection paths are tested.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter():
    return AnkiDirectAdapter(anki_base=Path("/tmp/anki"))


@pytest.fixture
def mock_repo():
    """Patch AnkiRepository so its context-manager yields a mock with col."""
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as mock:
        mock_instance = mock.return_value
        mock_instance.__enter__.return_value = mock_instance
        yield mock_instance


# ---------------------------------------------------------------------------
# get_deck_names
# ---------------------------------------------------------------------------


def _decks(*names):
    return [SimpleNamespace(name=n, id=i) for i, n in enumerate(names, 1)]


@pytest.mark.asyncio
async def test_get_deck_names(adapter, mock_repo):
    mock_repo.col.decks.all_names_and_ids.return_value = _decks("Default", "Math")
    names = await adapter.get_deck_names()
    assert "Math" in names
    mock_repo.col.decks.all_names_and_ids.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_deck_names_returns_full_list(adapter, mock_repo):
    mock_repo.col.decks.all_names_and_ids.return_value = _decks("Default", "Math", "Math::Calc")
    names = await adapter.get_deck_names()
    assert names == ["Default", "Math", "Math::Calc"]
    assert len(names) == 3


# ---------------------------------------------------------------------------
# get_notes_in_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_notes_in_deck(adapter, mock_repo):
    mock_repo.find_notes.return_value = [101, 102]
    res = await adapter.get_notes_in_deck("MyDeck")
    assert res == {"101": 101, "102": 102}
    mock_repo.find_notes.assert_called_with('"deck:MyDeck"')


# ---------------------------------------------------------------------------
# delete_notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_notes(adapter, mock_repo):
    result = await adapter.delete_notes([1, 2, 3])
    assert result is True
    mock_repo.col.remove_notes.assert_called_once()


# ---------------------------------------------------------------------------
# delete_decks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_decks(adapter, mock_repo):
    mock_repo.col.decks.id.return_value = 42
    result = await adapter.delete_decks(["OldDeck"])
    assert result is True
    mock_repo.col.decks.remove.assert_called_once_with([42])


# ---------------------------------------------------------------------------
# get_model_names
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# get_model_styling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_styling_success(adapter, mock_repo):
    mock_repo.col.models.by_name.return_value = {"css": ".card { font-size: 20px; }"}
    css = await adapter.get_model_styling("Basic")
    assert css == ".card { font-size: 20px; }"


# ---------------------------------------------------------------------------
# get_model_templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_templates_success(adapter, mock_repo):
    mock_repo.col.models.by_name.return_value = {
        "tmpls": [
            {"name": "Card 1", "qfmt": "Q1", "afmt": "A1"},
            {"name": "Card 2", "qfmt": "Q2", "afmt": "A2"},
        ]
    }
    templates = await adapter.get_model_templates("Basic")
    assert len(templates) == 2
    assert templates["Card 1"]["Front"] == "Q1"
    assert templates["Card 1"]["Back"] == "A1"
    assert templates["Card 2"]["Front"] == "Q2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,expected",
    [
        pytest.param("get_model_styling", "", id="styling"),
        pytest.param("get_model_templates", {}, id="templates"),
    ],
)
async def test_model_not_found_returns_empty(adapter, mock_repo, method, expected):
    """When model doesn't exist, return empty default."""
    mock_repo.col.models.by_name.return_value = None
    result = await getattr(adapter, method)("NonExistent")
    assert result == expected


# ---------------------------------------------------------------------------
# suspend_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_cards_success(adapter, mock_repo):
    mock_repo.col.sched.suspend_cards = MagicMock()
    result = await adapter.suspend_cards([1, 2])
    assert result is True
    mock_repo.col.sched.suspend_cards.assert_called_with([1, 2])


@pytest.mark.asyncio
async def test_suspend_cards_error(adapter):
    """When sched.suspend_cards raises, return False."""
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as MockRepo:
        instance = MockRepo.return_value.__enter__.return_value
        instance.col = MagicMock()
        instance.col.sched.suspend_cards.side_effect = Exception("Sched Error")
        res = await adapter.suspend_cards([1, 2])
        assert res is False


# ---------------------------------------------------------------------------
# unsuspend_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsuspend_cards_success(adapter, mock_repo):
    mock_repo.col.sched.unsuspend_cards = MagicMock()
    result = await adapter.unsuspend_cards([1, 2])
    assert result is True
    mock_repo.col.sched.unsuspend_cards.assert_called_with([1, 2])


# ---------------------------------------------------------------------------
# get_due_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_due_cards(adapter):
    """Test all three query variants: no filter, with deck, and include_new."""
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as MockRepo:
        instance = MockRepo.return_value.__enter__.return_value
        instance.col = MagicMock()
        instance.find_notes.return_value = [101, 102]

        # Case 1: No filter
        res = await adapter.get_due_cards()
        assert res == [101, 102]
        instance.find_notes.assert_called_with("(is:due)")

        # Case 2: With deck filter
        res = await adapter.get_due_cards("Math::Calc")
        instance.find_notes.assert_called_with('deck:"Math::Calc" (is:due)')

        # Case 3: Include new cards
        res = await adapter.get_due_cards("Math::Calc", include_new=True)
        instance.find_notes.assert_called_with('deck:"Math::Calc" (is:due OR is:new)')


# ---------------------------------------------------------------------------
# map_nids_to_arete_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_nids_to_arete_ids(adapter):
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as MockRepo:
        instance = MockRepo.return_value.__enter__.return_value
        instance.col = MagicMock()

        note1 = MagicMock()
        note1.tags = ["arete_A", "hard"]
        note2 = MagicMock()
        note2.tags = ["other", "arete_B"]
        note3 = MagicMock()
        note3.tags = ["no_id"]

        instance.col.get_note.side_effect = [note1, note2, note3]

        res = await adapter.map_nids_to_arete_ids([1, 2, 3])
        assert res == ["arete_A", "arete_B"]
        assert len(res) == 2


@pytest.mark.asyncio
async def test_map_nids_to_arete_ids_handles_missing_note(adapter, mock_repo):
    """Error on one note should not abort remaining notes."""
    col = mock_repo.col
    note1 = MagicMock(tags=["arete_01ABC"])
    col.get_note.side_effect = [note1, Exception("Note not found")]

    result = await adapter.map_nids_to_arete_ids([100, 200])
    assert result == ["arete_01ABC"]


# ---------------------------------------------------------------------------
# get_card_ids_for_arete_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_card_ids_for_arete_ids_preserves_order(adapter, mock_repo):
    col = mock_repo.col
    col.find_cards.side_effect = [[300], [100], [200]]

    result = await adapter.get_card_ids_for_arete_ids(["arete_C", "arete_A", "arete_B"])
    assert result == [300, 100, 200]


@pytest.mark.asyncio
async def test_get_card_ids_for_arete_ids_deduplicates(adapter, mock_repo):
    col = mock_repo.col
    col.find_cards.side_effect = [[100, 200], [200, 300]]

    result = await adapter.get_card_ids_for_arete_ids(["arete_A", "arete_B"])
    assert result == [100, 200, 300]


@pytest.mark.asyncio
async def test_get_card_ids_for_arete_ids_multi_card_note(adapter, mock_repo):
    """A note with multiple cards (e.g., Cloze) should include all CIDs."""
    col = mock_repo.col
    col.find_cards.side_effect = [[100, 101, 102]]

    result = await adapter.get_card_ids_for_arete_ids(["arete_CLOZE"])
    assert result == [100, 101, 102]


# ---------------------------------------------------------------------------
# create_topo_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_topo_deck_no_collection(adapter):
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as MockRepo:
        repo_instance = MockRepo.return_value.__enter__.return_value
        repo_instance.col = None
        result = await adapter.create_topo_deck("Queue", [1, 2, 3])
    assert result is False


@pytest.mark.asyncio
async def test_create_topo_deck_creates_new_filtered_deck(adapter, mock_repo):
    col = mock_repo.col
    col.decks.id.return_value = None  # Deck doesn't exist
    col.decks.new_filtered.return_value = 42
    col.decks.get.return_value = {"dyn": 1, "terms": [], "resched": True}

    card0 = MagicMock(did=42)
    card1 = MagicMock(did=42)
    col.get_card.side_effect = [card0, card1]

    result = await adapter.create_topo_deck("Arete::Queue", [100, 200])
    assert result is True

    col.decks.new_filtered.assert_called_once_with("Arete::Queue")
    col.sched.rebuild_filtered_deck.assert_called_once_with(42)
    assert card0.due == 1000
    assert card1.due == 1001
    assert col.update_card.call_count == 2


@pytest.mark.asyncio
async def test_create_topo_deck_empties_existing_deck(adapter, mock_repo):
    """Existing filtered deck should be emptied before rebuilding."""
    col = mock_repo.col
    col.decks.id.return_value = 42  # Deck exists
    col.decks.get.return_value = {"dyn": 1, "terms": [], "resched": True}

    card = MagicMock(did=42)
    col.get_card.return_value = card

    result = await adapter.create_topo_deck("Arete::Queue", [100])
    assert result is True

    col.sched.empty_filtered_deck.assert_called_once_with(42)
    col.sched.rebuild_filtered_deck.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_create_topo_deck_rejects_non_dynamic_deck(adapter, mock_repo):
    col = mock_repo.col
    col.decks.id.return_value = 42
    col.decks.get.return_value = {"dyn": 0}  # Standard deck, not filtered

    result = await adapter.create_topo_deck("Arete::Queue", [100])
    assert result is False
    col.sched.rebuild_filtered_deck.assert_not_called()


@pytest.mark.asyncio
async def test_create_topo_deck_skips_card_not_in_deck(adapter, mock_repo):
    """Cards not pulled into the filtered deck (e.g., suspended) should be skipped."""
    col = mock_repo.col
    col.decks.id.return_value = None
    col.decks.new_filtered.return_value = 42
    col.decks.get.return_value = {"dyn": 1, "terms": [], "resched": True}

    card0 = MagicMock(did=42)
    card1 = MagicMock(did=99)  # Different deck
    card2 = MagicMock(did=42)
    col.get_card.side_effect = [card0, card1, card2]

    result = await adapter.create_topo_deck("Queue", [100, 200, 300])
    assert result is True

    assert card0.due == 1000
    assert card2.due == 1002
    col.update_card.assert_any_call(card0)
    col.update_card.assert_any_call(card2)
    assert col.update_card.call_count == 2


@pytest.mark.asyncio
async def test_create_topo_deck_per_card_error_resilience(adapter, mock_repo):
    """A single card failure should not abort ordering of remaining cards."""
    col = mock_repo.col
    col.decks.id.return_value = None
    col.decks.new_filtered.return_value = 42
    col.decks.get.return_value = {"dyn": 1, "terms": [], "resched": True}

    card0 = MagicMock(did=42)
    card2 = MagicMock(did=42)
    col.get_card.side_effect = [card0, Exception("Card not found"), card2]

    result = await adapter.create_topo_deck("Queue", [100, 200, 300])
    assert result is True

    assert card0.due == 1000
    assert card2.due == 1002
    assert col.update_card.call_count == 2


# ---------------------------------------------------------------------------
# gui_browse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gui_browse_timeout(adapter):
    """When AnkiConnect never responds, gui_browse returns False."""
    with patch("sys.platform", "linux"):
        with patch("subprocess.run") as mock_run:
            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post.side_effect = Exception(
                    "No AnkiConnect"
                )
                with patch("asyncio.sleep", AsyncMock()):
                    res = await adapter.gui_browse("query")
                    assert res is False
                    mock_run.assert_called()


@pytest.mark.asyncio
async def test_gui_browse_macos(adapter):
    with patch("sys.platform", "darwin"):
        with patch("subprocess.run") as mock_run:
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"result": True, "error": None}
                mock_client.post.return_value = mock_resp

                with patch("asyncio.sleep", AsyncMock()):
                    res = await adapter.gui_browse("query")
                    assert res is True
                    mock_run.assert_called_with(["open", "-a", "Anki"], stdout=ANY, stderr=ANY)


@pytest.mark.asyncio
async def test_gui_browse_windows(adapter):
    with patch("sys.platform", "win32"):
        with patch("os.startfile", create=True) as mock_start:
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"result": True, "error": None}
                mock_client.post.return_value = mock_resp

                with patch("asyncio.sleep", AsyncMock()):
                    res = await adapter.gui_browse("query")
                    assert res is True
                    mock_start.assert_called()


@pytest.mark.asyncio
async def test_gui_browse_polling(adapter):
    """AnkiConnect responds on first poll — cross-platform."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": True, "error": None}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with (
        patch("subprocess.run") as mock_run,
        patch(
            "httpx.AsyncClient",
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_client)),
        ),
        patch("os.startfile", create=True) as mock_startfile,
        patch("asyncio.sleep", AsyncMock()),
    ):
        import sys

        res = await adapter.gui_browse("test query")
        assert res is True
        if sys.platform == "win32":
            mock_startfile.assert_called()
        else:
            mock_run.assert_called()


# ---------------------------------------------------------------------------
# no-collection parametrized fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("get_deck_names", [], []),
        ("get_notes_in_deck", ["D"], {}),
        ("delete_notes", [[1]], False),
        ("delete_decks", [["D"]], False),
        ("get_model_styling", ["Basic"], ""),
        ("get_model_templates", ["Basic"], {}),
        ("unsuspend_cards", [[1]], False),
        ("suspend_cards", [[1]], False),
    ],
    ids=[
        "get_deck_names",
        "get_notes_in_deck",
        "delete_notes",
        "delete_decks",
        "get_model_styling",
        "get_model_templates",
        "unsuspend_cards",
        "suspend_cards",
    ],
)
async def test_no_collection_fallback(adapter, method, args, expected):
    """Parametrized: each method returns its safe default when col is None."""
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.col = None

        result = await getattr(adapter, method)(*args)
        assert result == expected


@pytest.mark.asyncio
async def test_get_card_ids_for_arete_ids_dedupes_without_rescanning(adapter, mock_repo):
    """Dedup is a set lookup per card, not a scan of the cards found so far."""
    comparisons = 0

    class Cid(int):
        def __eq__(self, other):
            nonlocal comparisons
            comparisons += 1
            return int(self) == int(other)

        __hash__ = int.__hash__

    cids = [Cid(i) for i in range(2000)]
    mock_repo.col.find_cards.side_effect = [[c] for c in cids]

    result = await adapter.get_card_ids_for_arete_ids([f"arete_{i}" for i in range(2000)])

    assert result == cids
    assert comparisons < 100  # a list scan makes about 2000 * 1999 / 2
