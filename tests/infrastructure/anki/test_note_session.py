"""Unit tests for DirectNoteSession edge cases on a real throwaway collection."""

from pathlib import Path

import pytest
from anki.collection import Collection

from arete.domain.models import NewNote, NoteChange
from arete.infrastructure.anki.note_session import DirectNoteSession


@pytest.mark.asyncio
async def test_direct_file_rejects_filtered_deck(tmp_path: Path):
    col = Collection(str(tmp_path / "col.anki2"))
    try:
        home_did = col.decks.id("Home")
        m_basic = col.models.by_name("Basic")
        assert m_basic is not None
        note = col.new_note(m_basic)
        note["Front"] = "Q"
        note["Back"] = "A"
        col.add_note(note, home_did)
        cid = note.card_ids()[0]
        col.decks.new_filtered("Arete::Queue")

        session = DirectNoteSession(col)
        [err] = await session.apply_changes(
            [NoteChange(int(note.id), home_deck=((cid,), "Arete::Queue"))]
        )
        assert isinstance(err, ValueError)
        assert "filtered deck" in str(err)
    finally:
        col.close()


@pytest.mark.asyncio
async def test_direct_create_removes_zero_card_orphan_note(tmp_path: Path):
    """If add_note leaves a note with 0 cards, DirectNoteSession removes the orphan and raises."""

    class _ZeroCardCollection:
        def __init__(self, inner: Collection):
            self._inner = inner
            self.models = inner.models
            self.decks = inner.decks

        def new_note(self, notetype):
            note = self._inner.new_note(notetype)
            note.card_ids = lambda: []  # type: ignore[method-assign]
            return note

        def add_note(self, note, deck_id):
            return self._inner.add_note(note, deck_id)

        def remove_notes(self, nids):
            return self._inner.remove_notes(nids)

    col = Collection(str(tmp_path / "col.anki2"))
    try:
        session = DirectNoteSession(_ZeroCardCollection(col))  # type: ignore[arg-type]
        [res] = await session.create_notes(
            [NewNote("Basic", "Default", {"Front": "Q", "Back": "A"}, ())]
        )
        assert isinstance(res, ValueError)
        assert "empty" in str(res)
        assert col.db.scalar("select count(*) from notes") == 0
    finally:
        col.close()
