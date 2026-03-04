"""Tests for arete.domain.models — AnkiNote, AnkiDeck, and other domain models."""

from pathlib import Path

import pytest

from arete.domain.models import (
    AnkiCardStats,
    AnkiDeck,
    AnkiNote,
    UpdateItem,
    WorkItem,
)


# ---------------------------------------------------------------------------
# AnkiDeck
# ---------------------------------------------------------------------------


class TestAnkiDeck:
    """AnkiDeck.parents returns all ancestor decks in the :: hierarchy."""

    def test_parents_single_level(self):
        deck = AnkiDeck(name="Math")
        assert deck.parents == []

    def test_parents_two_levels(self):
        deck = AnkiDeck(name="Math::Algebra")
        assert deck.parents == ["Math"]

    def test_parents_three_levels(self):
        deck = AnkiDeck(name="A::B::C")
        assert deck.parents == ["A", "A::B"]

    def test_parents_four_levels(self):
        deck = AnkiDeck(name="A::B::C::D")
        assert deck.parents == ["A", "A::B", "A::B::C"]

    def test_parents_order_preserved(self):
        """Parents should be returned shallowest-first."""
        deck = AnkiDeck(name="X::Y::Z")
        parents = deck.parents
        assert parents[0] == "X"
        assert parents[1] == "X::Y"

    def test_parents_does_not_include_self(self):
        deck = AnkiDeck(name="A::B::C")
        assert "A::B::C" not in deck.parents


# ---------------------------------------------------------------------------
# AnkiNote
# ---------------------------------------------------------------------------


class TestAnkiNote:
    """AnkiNote creation and serialisation round-trip."""

    @pytest.fixture
    def basic_note(self) -> AnkiNote:
        return AnkiNote(
            model="Basic",
            deck="Test::Deck",
            fields={"Front": "What is 2+2?", "Back": "4"},
            tags=["math", "arithmetic"],
            start_line=10,
            end_line=15,
            source_file=Path("/vault/note.md"),
            source_index=1,
        )

    def test_required_fields(self, basic_note: AnkiNote):
        assert basic_note.model == "Basic"
        assert basic_note.deck == "Test::Deck"
        assert basic_note.fields["Front"] == "What is 2+2?"
        assert basic_note.tags == ["math", "arithmetic"]
        assert basic_note.start_line == 10
        assert basic_note.end_line == 15
        assert basic_note.source_file == Path("/vault/note.md")
        assert basic_note.source_index == 1

    def test_optional_fields_default_none(self, basic_note: AnkiNote):
        assert basic_note.nid is None
        assert basic_note.cid is None
        assert basic_note.content_hash is None

    def test_optional_fields_set(self):
        note = AnkiNote(
            model="Cloze",
            deck="D",
            fields={"Text": "{{c1::answer}}"},
            tags=[],
            start_line=1,
            end_line=2,
            source_file=Path("x.md"),
            source_index=1,
            nid="12345",
            cid="67890",
            content_hash="abc123",
        )
        assert note.nid == "12345"
        assert note.cid == "67890"
        assert note.content_hash == "abc123"

    def test_to_dict_converts_path(self, basic_note: AnkiNote):
        d = basic_note.to_dict()
        assert isinstance(d["source_file"], str)
        assert d["source_file"] == "/vault/note.md"
        assert d["model"] == "Basic"
        assert d["tags"] == ["math", "arithmetic"]

    def test_from_dict_round_trip(self, basic_note: AnkiNote):
        d = basic_note.to_dict()
        restored = AnkiNote.from_dict(d)

        assert restored.model == basic_note.model
        assert restored.deck == basic_note.deck
        assert restored.fields == basic_note.fields
        assert restored.tags == basic_note.tags
        assert restored.source_file == basic_note.source_file
        assert restored.source_index == basic_note.source_index
        assert isinstance(restored.source_file, Path)

    def test_from_dict_preserves_nid_cid(self):
        note = AnkiNote(
            model="Basic",
            deck="D",
            fields={"Front": "Q", "Back": "A"},
            tags=[],
            start_line=1,
            end_line=2,
            source_file=Path("f.md"),
            source_index=1,
            nid="111",
            cid="222",
        )
        d = note.to_dict()
        restored = AnkiNote.from_dict(d)
        assert restored.nid == "111"
        assert restored.cid == "222"


# ---------------------------------------------------------------------------
# AnkiCardStats
# ---------------------------------------------------------------------------


class TestAnkiCardStats:
    def test_creation_required_fields(self):
        stats = AnkiCardStats(
            card_id=1,
            note_id=2,
            lapses=3,
            ease=2500,
            difficulty=0.5,
            deck_name="Test",
            interval=30,
            due=1700000000,
            reps=10,
        )
        assert stats.card_id == 1
        assert stats.lapses == 3
        assert stats.ease == 2500
        assert stats.difficulty == 0.5
        assert stats.average_time == 0  # default
        assert stats.front is None  # default

    def test_optional_defaults(self):
        stats = AnkiCardStats(
            card_id=1,
            note_id=2,
            lapses=0,
            ease=2500,
            difficulty=None,
            deck_name="D",
            interval=1,
            due=0,
            reps=0,
        )
        assert stats.average_time == 0
        assert stats.front is None


# ---------------------------------------------------------------------------
# WorkItem
# ---------------------------------------------------------------------------


class TestWorkItem:
    def test_work_item_creation(self):
        note = AnkiNote(
            model="Basic",
            deck="D",
            fields={"Front": "Q"},
            tags=[],
            start_line=1,
            end_line=2,
            source_file=Path("f.md"),
            source_index=1,
        )
        item = WorkItem(note=note, source_file=Path("f.md"), source_index=1)
        assert item.note is note
        assert item.source_file == Path("f.md")
        assert item.source_index == 1


# ---------------------------------------------------------------------------
# UpdateItem
# ---------------------------------------------------------------------------


class TestUpdateItem:
    def test_successful_update(self):
        item = UpdateItem(
            source_file=Path("f.md"),
            source_index=1,
            new_nid="100",
            new_cid="200",
            ok=True,
        )
        assert item.ok is True
        assert item.error is None
        assert item.note is None

    def test_failed_update(self):
        item = UpdateItem(
            source_file=Path("f.md"),
            source_index=1,
            new_nid=None,
            new_cid=None,
            ok=False,
            error="Model not found",
        )
        assert item.ok is False
        assert item.error == "Model not found"
