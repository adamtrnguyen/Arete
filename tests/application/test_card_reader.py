"""Tests for card_reader application service."""

from pathlib import Path

from arete.application.card_reader import (
    ConceptCardsResult,
    FileCardsResult,
    _extract_card_entry,
    find_concept_file,
    get_concept_cards,
    get_note_body,
    list_file_cards,
)

# ---------------------------------------------------------------------------
# find_concept_file
# ---------------------------------------------------------------------------


class TestFindConceptFile:
    def test_exact_match(self, tmp_path: Path):
        (tmp_path / "Algebra.md").write_text("# Algebra")
        result = find_concept_file(tmp_path, "Algebra")
        assert result == tmp_path / "Algebra.md"

    def test_case_insensitive_match(self, tmp_path: Path):
        (tmp_path / "Algebra.md").write_text("# Algebra")
        result = find_concept_file(tmp_path, "algebra")
        assert result is not None
        # On case-insensitive FS (macOS), exact match finds it first
        assert result.stem.lower() == "algebra"

    def test_subdirectory_search(self, tmp_path: Path):
        sub = tmp_path / "topics"
        sub.mkdir()
        (sub / "calculus.md").write_text("# Calculus")
        result = find_concept_file(tmp_path, "calculus")
        assert result is not None
        assert result.name == "calculus.md"

    def test_skips_hidden_dirs(self, tmp_path: Path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("# Secret")
        result = find_concept_file(tmp_path, "secret")
        assert result is None

    def test_not_found(self, tmp_path: Path):
        result = find_concept_file(tmp_path, "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# _extract_card_entry
# ---------------------------------------------------------------------------


class TestExtractCardEntry:
    def test_basic_extraction(self):
        card = {"id": "arete_001", "model": "Basic", "Front": "Q?", "Back": "A."}
        entry = _extract_card_entry(card, 1, "TestDeck")
        assert entry["index"] == 1
        assert entry["arete_id"] == "arete_001"
        assert entry["model"] == "Basic"
        assert entry["deck"] == "TestDeck"
        assert entry["Front"] == "Q?"
        assert entry["Back"] == "A."

    def test_lowercase_fields(self):
        card = {"front": "Q?", "back": "A."}
        entry = _extract_card_entry(card, 0, "Deck")
        assert entry["Front"] == "Q?"
        assert entry["Back"] == "A."

    def test_with_deps(self):
        card = {"deps": {"requires": ["arete_002"]}}
        entry = _extract_card_entry(card, 0, "Deck")
        assert entry["deps"] == {"requires": ["arete_002"]}

    def test_no_optional_fields(self):
        card = {}
        entry = _extract_card_entry(card, 0, "Deck")
        assert "arete_id" not in entry
        assert "model" not in entry
        assert "deps" not in entry
        assert entry["deck"] == "Deck"


# ---------------------------------------------------------------------------
# get_concept_cards
# ---------------------------------------------------------------------------

CONCEPT_MD = """\
---
arete: true
deck: TestDeck
cards:
  - id: arete_001
    model: Basic
    Front: "What is X?"
    Back: "X is Y."
    deps:
      requires: [arete_002]
  - id: arete_002
    model: Basic
    Front: "What is Y?"
    Back: "Y is Z."
    deck: OtherDeck
---

# Test Concept
"""


class TestGetConceptCards:
    def test_success(self, tmp_path: Path):
        (tmp_path / "TestConcept.md").write_text(CONCEPT_MD)
        result = get_concept_cards(tmp_path, "TestConcept")
        assert isinstance(result, ConceptCardsResult)
        assert result.concept == "TestConcept"
        assert result.card_count == 2
        assert result.cards[0]["arete_id"] == "arete_001"

    def test_deck_filter(self, tmp_path: Path):
        (tmp_path / "TestConcept.md").write_text(CONCEPT_MD)
        result = get_concept_cards(tmp_path, "TestConcept", deck_filter="Other")
        assert isinstance(result, ConceptCardsResult)
        assert result.card_count == 1
        assert result.cards[0]["arete_id"] == "arete_002"

    def test_deck_filter_no_match(self, tmp_path: Path):
        (tmp_path / "TestConcept.md").write_text(CONCEPT_MD)
        result = get_concept_cards(tmp_path, "TestConcept", deck_filter="Nonexistent")
        assert isinstance(result, str)
        assert "No cards matched" in result

    def test_concept_not_found(self, tmp_path: Path):
        result = get_concept_cards(tmp_path, "Missing")
        assert isinstance(result, str)
        assert "No vault note found" in result

    def test_yaml_error(self, tmp_path: Path):
        (tmp_path / "Bad.md").write_text("---\ninvalid: [unclosed\n---\n")
        result = get_concept_cards(tmp_path, "Bad")
        assert isinstance(result, str)
        assert "Error parsing" in result

    def test_no_cards_field(self, tmp_path: Path):
        (tmp_path / "Empty.md").write_text("---\narete: true\ndeck: Test\n---\n")
        result = get_concept_cards(tmp_path, "Empty")
        assert isinstance(result, str)
        assert "No cards found" in result

    def test_skips_non_dict_cards(self, tmp_path: Path):
        md = "---\narete: true\ndeck: Test\ncards:\n  - not_a_dict\n  - id: arete_001\n    Front: Q\n---\n"
        (tmp_path / "Mixed.md").write_text(md)
        result = get_concept_cards(tmp_path, "Mixed")
        assert isinstance(result, ConceptCardsResult)
        assert result.card_count == 1


# ---------------------------------------------------------------------------
# list_file_cards
# ---------------------------------------------------------------------------

FILE_MD = """\
---
arete: true
deck: FileDeck
tags: [math, algebra]
model: Cloze
cards:
  - id: arete_001
    Front: "Q1"
    Back: "A1"
    deps:
      requires: [arete_002]
      related: [arete_003]
  - id: arete_002
    model: Basic
    deck: OverrideDeck
    tags: [override]
    Front: "Q2"
    Back: "A2"
---
"""


class TestListFileCards:
    def test_success(self, tmp_path: Path):
        p = tmp_path / "test.md"
        p.write_text(FILE_MD)
        result = list_file_cards(p)
        assert isinstance(result, FileCardsResult)
        assert result.basename == "test"
        assert result.deck == "FileDeck"
        assert result.tags == ["math", "algebra"]
        assert result.card_count == 2
        # First card inherits file model
        assert result.cards[0]["model"] == "Cloze"
        assert result.cards[0]["deps"]["requires"] == ["arete_002"]
        # Second card overrides
        assert result.cards[1]["model"] == "Basic"
        assert result.cards[1]["deck"] == "OverrideDeck"

    def test_file_not_found(self, tmp_path: Path):
        result = list_file_cards(tmp_path / "nope.md")
        assert isinstance(result, str)
        assert "File not found" in result

    def test_yaml_error(self, tmp_path: Path):
        p = tmp_path / "bad.md"
        p.write_text("---\ninvalid: [unclosed\n---\n")
        result = list_file_cards(p)
        assert isinstance(result, str)
        assert "Failed to parse" in result

    def test_not_arete_note(self, tmp_path: Path):
        p = tmp_path / "plain.md"
        p.write_text("---\ntitle: Plain Note\n---\n# Just a note")
        result = list_file_cards(p)
        assert isinstance(result, str)
        assert "not an Arete note" in result

    def test_deps_not_dict(self, tmp_path: Path):
        md = "---\narete: true\ncards:\n  - id: c1\n    deps: invalid\n---\n"
        p = tmp_path / "baddeps.md"
        p.write_text(md)
        result = list_file_cards(p)
        assert isinstance(result, FileCardsResult)
        assert result.cards[0]["deps"]["requires"] == []
        assert result.cards[0]["deps"]["related"] == []

    def test_skips_non_dict_cards(self, tmp_path: Path):
        md = "---\narete: true\ncards:\n  - not_a_dict\n  - id: c1\n---\n"
        p = tmp_path / "mixed.md"
        p.write_text(md)
        result = list_file_cards(p)
        assert isinstance(result, FileCardsResult)
        assert result.card_count == 1


# ---------------------------------------------------------------------------
# get_note_body
# ---------------------------------------------------------------------------


class TestGetNoteBody:
    def test_returns_body(self, tmp_path: Path):
        p = tmp_path / "note.md"
        p.write_text("---\narete: true\n---\n\n# Hello\n\nBody text here.")
        result = get_note_body(p)
        assert "# Hello" in result
        assert "Body text here." in result

    def test_file_not_found(self, tmp_path: Path):
        result = get_note_body(tmp_path / "nope.md")
        assert result.startswith("Error:")

    def test_empty_body(self, tmp_path: Path):
        p = tmp_path / "empty.md"
        p.write_text("---\narete: true\n---\n")
        result = get_note_body(p)
        assert result == "(empty body)"

    def test_no_frontmatter(self, tmp_path: Path):
        p = tmp_path / "plain.md"
        p.write_text("Just plain text, no frontmatter.")
        result = get_note_body(p)
        assert "Just plain text" in result
