import pickle
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arete.domain.models import AnkiNote
from arete.infrastructure.anki.repository import AnkiRepository


@pytest.fixture
def mock_col():
    with patch("arete.infrastructure.anki.repository.Collection") as mock:
        yield mock


@pytest.fixture
def repo():
    return AnkiRepository(base_path=Path("/tmp/anki"))


def test_resolve_collection_path_explicit_profile(repo):
    repo.profile_name = "User1"
    # Mock existence of prefs db
    with patch.object(Path, "exists", return_value=True):
        path = repo._resolve_collection_path()
    assert path == Path("/tmp/anki") / "User1" / "collection.anki2"


@patch("arete.infrastructure.anki.repository.sqlite3")
def test_resolve_collection_path_auto(mock_sqlite, repo):
    # Mock prefs21.db
    mock_conn = MagicMock()
    mock_sqlite.connect.return_value = mock_conn

    # Mock global profile data
    mock_cursor = MagicMock()
    mock_conn.execute.return_value = mock_cursor

    data = {"last_loaded_profile_name": "UserAuto"}
    blob = pickle.dumps(data)
    mock_cursor.fetchone.return_value = (blob,)

    with patch.object(Path, "exists", return_value=True):
        path = repo._resolve_collection_path()

    assert path == Path("/tmp/anki") / "UserAuto" / "collection.anki2"
    assert repo.profile_name == "UserAuto"


def test_context_manager(mock_col, repo):
    # Setup mock collection instance
    mock_instance = MagicMock()
    mock_col.return_value = mock_instance  # When Collection() is called, return this

    with patch.object(repo, "_resolve_collection_path", return_value=Path("/tmp/c.anki2")):
        with repo as r:
            assert r.col is mock_instance
            mock_col.assert_called_with(str(Path("/tmp/c.anki2")))

        # Exit
        mock_instance.close.assert_called_once()
        assert r.col is None


def test_add_note(mock_col, repo):
    # Setup mock collection
    repo.col = MagicMock()

    # Mock model
    mock_model = {"flds": [{"name": "Front"}, {"name": "Back"}]}
    repo.col.models.by_name.return_value = mock_model

    # Mock new note
    mock_note = MagicMock()
    mock_note.id = 999
    repo.col.new_note.return_value = mock_note

    # Mock deck id
    repo.col.decks.id.return_value = 1

    # Input
    note_data = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Q", "Back": "A"},
        tags=["t1"],
        start_line=1,
        end_line=1,
        source_file=Path("f.md"),
        source_index=1,
    )

    nid = repo.add_note(note_data)

    assert nid == 999
    repo.col.add_note.assert_called()
    mock_note.__setitem__.assert_any_call("Front", "Q")
    mock_note.add_tag.assert_called_with("t1")


def test_update_note(mock_col, repo):
    repo.col = MagicMock()

    # Mock existing note
    mock_note = MagicMock()
    mock_note.note_type.return_value = {"name": "Basic", "flds": [{"name": "Front"}]}
    mock_note.cards.return_value = [MagicMock(did=1)]  # old deck
    mock_note.tags = ["old_tag"]
    mock_note.__getitem__.return_value = "Old Q"
    repo.col.get_note.return_value = mock_note

    # Mock decks
    repo.col.decks.id.return_value = 2  # new deck

    # Input
    note_data = AnkiNote(
        model="Basic",
        deck="NewDeck",
        fields={"Front": "New Q"},
        tags=["new_tag"],
        start_line=1,
        end_line=1,
        source_file=Path("f.md"),
        source_index=1,
    )

    updated = repo.update_note(123, note_data)

    assert updated is True
    # Check field update
    mock_note.__setitem__.assert_called_with("Front", "New Q")
    # Check deck move
    repo.col.set_deck.assert_called()
    # Check tag update
    assert "new_tag" in mock_note.tags
    repo.col.update_note.assert_called_with(mock_note)
