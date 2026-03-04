"""Consolidated unit tests for AnkiDirectAdapter.

Every public method of AnkiDirectAdapter is covered here, organized by
method name.  Both success and failure / no-collection paths are tested.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, patch

import pytest

from arete.domain.models import AnkiDeck, AnkiNote, WorkItem
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
# Helpers
# ---------------------------------------------------------------------------


def _make_note(
    *,
    model: str = "Basic",
    deck: str = "Default",
    fields: dict | None = None,
    tags: list | None = None,
    nid: str | None = None,
    source_file: str = "test.md",
    source_index: int = 1,
    start_line: int = 1,
    end_line: int = 5,
) -> AnkiNote:
    return AnkiNote(
        model=model,
        deck=deck,
        fields=fields or {"Front": "Q", "Back": "A"},
        tags=tags or [],
        start_line=start_line,
        end_line=end_line,
        source_file=Path(source_file),
        source_index=source_index,
        nid=nid,
    )


def _make_item(note: AnkiNote, *, source_index: int | None = None) -> WorkItem:
    idx = source_index if source_index is not None else note.source_index
    return WorkItem(source_file=note.source_file, source_index=idx, note=note)


# ---------------------------------------------------------------------------
# get_deck_names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deck_names(adapter, mock_repo):
    mock_repo.col.decks.all_names.return_value = ["Default", "Math"]
    names = await adapter.get_deck_names()
    assert "Math" in names
    mock_repo.col.decks.all_names.assert_called_once()


@pytest.mark.asyncio
async def test_get_deck_names_returns_full_list(adapter, mock_repo):
    mock_repo.col.decks.all_names.return_value = ["Default", "Math", "Math::Calc"]
    names = await adapter.get_deck_names()
    assert names == ["Default", "Math", "Math::Calc"]
    assert len(names) == 3


# ---------------------------------------------------------------------------
# ensure_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_deck(adapter, mock_repo):
    mock_repo.col.decks.id.return_value = 1
    result = await adapter.ensure_deck("New Deck")
    assert result is True
    mock_repo.col.decks.id.assert_called_with("New Deck")


@pytest.mark.asyncio
async def test_ensure_deck_with_anki_deck_object(adapter, mock_repo):
    """ensure_deck should accept an AnkiDeck instance, not just a string."""
    mock_repo.col.decks.id.return_value = 1
    deck = AnkiDeck(name="Science::Physics")
    result = await adapter.ensure_deck(deck)
    assert result is True
    mock_repo.col.decks.id.assert_called_with("Science::Physics")


# ---------------------------------------------------------------------------
# sync_notes — insert (add)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_notes_insert(adapter, mock_repo):
    note = _make_note()
    item = _make_item(note, source_index=0)
    mock_repo.add_note.return_value = 12345

    updates = await adapter.sync_notes([item])

    assert len(updates) == 1
    assert updates[0].ok is True
    assert updates[0].new_nid == "12345"
    mock_repo.add_note.assert_called_once()


@pytest.mark.asyncio
async def test_sync_notes_single_basic_note(adapter, mock_repo):
    """Sync a single new Basic note — verifies all result fields."""
    note = _make_note(
        fields={"Front": "Capital of France?", "Back": "Paris"},
        tags=["geography"],
        source_file="/vault/geo.md",
    )
    item = _make_item(note)
    mock_repo.add_note.return_value = 99999

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "99999"
    assert results[0].source_file == Path("/vault/geo.md")
    assert results[0].source_index == 1
    assert results[0].note is note


@pytest.mark.asyncio
async def test_sync_notes_add_when_nid_is_none_and_update_returns_false(adapter, mock_repo):
    """When note has no nid, update returns False (not found), it should add."""
    note = _make_note(nid=None)
    item = _make_item(note, source_index=0)
    mock_repo.update_note.return_value = False
    mock_repo.add_note.return_value = 12345

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "12345"
    mock_repo.add_note.assert_called_once()


# ---------------------------------------------------------------------------
# sync_notes — update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_notes_update(adapter, mock_repo):
    """When NID exists and update succeeds, add_note should NOT be called."""
    note = _make_note(nid="999")
    item = _make_item(note, source_index=0)
    mock_repo.update_note.return_value = True

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "999"
    mock_repo.update_note.assert_called_once()
    mock_repo.add_note.assert_not_called()


@pytest.mark.asyncio
async def test_sync_notes_update_not_found_falls_through_to_add(adapter, mock_repo):
    """When update_note returns False (NID not found in Anki DB), fall through to add."""
    note = _make_note(nid="9999")
    item = _make_item(note, source_index=1)
    mock_repo.update_note.return_value = False
    mock_repo.add_note.return_value = 55555

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "55555"
    mock_repo.update_note.assert_called_once()
    mock_repo.add_note.assert_called_once()


# ---------------------------------------------------------------------------
# sync_notes — failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_notes_add_failure(adapter, mock_repo):
    """When add_note raises, the result should reflect the error."""
    note = _make_note(nid=None)
    item = _make_item(note, source_index=1)
    mock_repo.add_note.side_effect = Exception("Model not found")

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is False
    assert "Add failed" in results[0].error


@pytest.mark.asyncio
async def test_sync_notes_update_exception_sets_error(adapter, mock_repo):
    """When update_note raises, the error_msg is set and add is NOT attempted."""
    note = _make_note(nid="1", fields={})
    item = _make_item(note, source_index=1)
    mock_repo.update_note.side_effect = Exception("Update Failed")

    results = await adapter.sync_notes([item])

    assert results[0].ok is False
    assert results[0].error == "Update Failed"


@pytest.mark.asyncio
async def test_sync_notes_item_failure(adapter, mock_repo):
    """Test individual item failure (catch-all in the loop)."""
    item = WorkItem(note=MagicMock(nid="123"), source_file=Path("x"), source_index=0)
    mock_repo.update_note.side_effect = Exception("Update Failed")

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is False
    assert "Unexpected" in results[0].error or "Update Failed" in results[0].error


@pytest.mark.asyncio
async def test_sync_notes_loop_exception_critical(adapter, mock_repo):
    """Property access on WorkItem raises — caught by outer catch-all."""
    item = MagicMock()
    type(item).note = PropertyMock(side_effect=Exception("Critical Fail"))
    item.source_file = Path("f.md")
    item.source_index = 0

    results = await adapter.sync_notes([item])
    assert results[0].ok is False
    assert "Unexpected error" in results[0].error


@pytest.mark.asyncio
async def test_sync_notes_db_failure(adapter):
    """Test full failure if DB cannot be opened (AnkiRepository __enter__ raises)."""
    item = WorkItem(source_file=Path("x"), source_index=0, note=MagicMock())

    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as MockRepo:
        MockRepo.return_value.__enter__.side_effect = Exception("DB Locked")

        results = await adapter.sync_notes([item])

        assert len(results) == 1
        assert results[0].ok is False
        assert "DB Error" in results[0].error
        assert "DB Locked" in results[0].error


@pytest.mark.asyncio
async def test_sync_notes_db_failure_constructor(adapter):
    """Test full failure if AnkiRepository constructor itself raises."""
    item = WorkItem(
        source_file=Path("f.md"), source_index=1, note=MagicMock()
    )

    with patch(
        "arete.infrastructure.adapters.anki_direct.AnkiRepository",
        side_effect=Exception("DB Lock"),
    ):
        results = await adapter.sync_notes([item])
        assert len(results) == 1
        assert results[0].ok is False
        assert "DB Error" in results[0].error


# ---------------------------------------------------------------------------
# sync_notes — batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_notes_multiple_items(adapter, mock_repo):
    """Batch sync: multiple items processed in sequence."""
    items = []
    for i in range(3):
        note = _make_note(
            fields={"Front": f"Q{i}"},
            source_file=f"file{i}.md",
            source_index=i + 1,
        )
        items.append(_make_item(note, source_index=i + 1))

    mock_repo.add_note.side_effect = [100, 200, 300]

    results = await adapter.sync_notes(items)

    assert len(results) == 3
    assert all(r.ok for r in results)
    assert [r.new_nid for r in results] == ["100", "200", "300"]


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


@pytest.mark.asyncio
async def test_get_model_names_returns_names(adapter, mock_repo):
    mock_repo.col.models.all.return_value = [
        {"name": "Basic"},
        {"name": "Cloze"},
    ]
    names = await adapter.get_model_names()
    assert names == ["Basic", "Cloze"]


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
# get_card_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_card_stats_success(adapter, mock_repo):
    """Normal path: fetch stats for a single NID."""
    mock_card = MagicMock()
    mock_card.id = 1001
    mock_card.nid = 500
    mock_card.lapses = 2
    mock_card.factor = 2500
    mock_card.ivl = 30
    mock_card.due = 1700000000
    mock_card.reps = 10
    mock_card.did = 1
    mock_card.memory_state = None  # no FSRS

    mock_deck = {"name": "TestDeck"}
    mock_repo.col.find_cards.return_value = [1001]
    mock_repo.col.get_card.return_value = mock_card
    mock_repo.col.decks.get.return_value = mock_deck

    mock_note = MagicMock()
    mock_note.fields = ["Front text"]
    mock_repo.col.get_note.return_value = mock_note

    stats = await adapter.get_card_stats([500])

    assert len(stats) == 1
    s = stats[0]
    assert s.card_id == 1001
    assert s.note_id == 500
    assert s.lapses == 2
    assert s.ease == 2500
    assert s.deck_name == "TestDeck"
    assert s.front == "Front text"
    assert s.difficulty is None


@pytest.mark.asyncio
async def test_get_card_stats_exception_returns_empty(adapter, mock_repo):
    """When get_card raises, return empty list (card-level exception handling)."""
    mock_repo.col.find_cards.side_effect = Exception("Card fail")
    res = await adapter.get_card_stats([999])
    assert res == []


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
# get_learning_insights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_learning_insights(adapter, mock_repo):
    mock_repo.col.find_cards.side_effect = [
        [1, 2, 3],  # total cards
        [2],  # troublesome cards
    ]

    mock_card = MagicMock()
    mock_card.nid = 100
    mock_card.lapses = 5
    mock_repo.col.get_card.return_value = mock_card

    mock_note = MagicMock()
    mock_note.note_type.return_value = {
        "name": "Basic",
        "flds": [{"name": "Front"}, {"name": "Back"}],
    }
    mock_note.fields = ["Bad Card", "Back Content"]
    mock_repo.col.get_note.return_value = mock_note

    stats = await adapter.get_learning_insights(lapse_threshold=3)

    assert stats.total_cards == 3
    assert len(stats.problematic_notes) == 1
    assert stats.problematic_notes[0].note_name == "Bad Card"
    assert stats.problematic_notes[0].lapses == 5


@pytest.mark.asyncio
async def test_insights_missing_model(adapter, mock_repo):
    """When note_type() returns None, the note is skipped."""
    mock_repo.col.find_cards.side_effect = lambda *args: [1]

    mock_card = MagicMock()
    mock_card.nid = 100
    mock_card.lapses = 5
    mock_repo.col.get_card.return_value = mock_card

    mock_note = MagicMock()
    mock_note.note_type.return_value = None
    mock_repo.col.get_note.return_value = mock_note

    stats = await adapter.get_learning_insights(lapse_threshold=3)
    assert len(stats.problematic_notes) == 0


@pytest.mark.asyncio
async def test_insights_fallback_name(adapter, mock_repo):
    """Note name falls back to first field value when _obsidian_source not present."""
    mock_repo.col.find_cards.side_effect = lambda *args: [1]
    mock_card = MagicMock()
    mock_card.nid = 100
    mock_card.lapses = 5
    mock_repo.col.get_card.return_value = mock_card

    mock_note = MagicMock()
    mock_note.note_type.return_value = {"name": "M", "flds": [{"name": "F"}]}
    mock_note.fields = ["First Field Value"]
    mock_repo.col.get_note.return_value = mock_note

    stats = await adapter.get_learning_insights(lapse_threshold=3)

    assert len(stats.problematic_notes) == 1
    assert stats.problematic_notes[0].note_name == "First Field Value"


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
                    mock_run.assert_called_with(
                        ["open", "-a", "Anki"], stdout=ANY, stderr=ANY
                    )


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
        ("get_model_names", [], []),
        ("ensure_deck", ["D"], False),
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
        "get_model_names",
        "ensure_deck",
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
