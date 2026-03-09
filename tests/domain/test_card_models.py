"""Tests for Pydantic card frontmatter models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arete.domain.card_models import (
    AnkiBlock,
    AreteFileMetadata,
    BasicCard,
    ClozeCard,
    CustomCard,
    DepsBlock,
    parse_card,
    parse_file_metadata,
)

# ===================================================================
# AnkiBlock
# ===================================================================


class TestAnkiBlock:
    def test_empty(self):
        block = AnkiBlock()
        assert block.nid is None
        assert block.cid is None

    def test_string_ids(self):
        block = AnkiBlock(nid="123", cid="456")
        assert block.nid == "123"
        assert block.cid == "456"

    def test_int_coercion(self):
        block = AnkiBlock(nid=123, cid=456)
        assert block.nid == "123"
        assert block.cid == "456"

    def test_whitespace_stripped(self):
        block = AnkiBlock(nid="  123  ", cid="  456  ")
        assert block.nid == "123"
        assert block.cid == "456"

    def test_empty_string_becomes_none(self):
        block = AnkiBlock(nid="", cid="   ")
        assert block.nid is None
        assert block.cid is None


# ===================================================================
# DepsBlock
# ===================================================================


class TestDepsBlock:
    def test_empty(self):
        block = DepsBlock()
        assert block.requires == []
        assert block.related == []

    def test_lists(self):
        block = DepsBlock(requires=["a", "b"], related=["c"])
        assert block.requires == ["a", "b"]
        assert block.related == ["c"]

    def test_string_coercion(self):
        block = DepsBlock(requires="single_dep")
        assert block.requires == ["single_dep"]

    def test_none_coercion(self):
        block = DepsBlock(requires=None, related=None)
        assert block.requires == []
        assert block.related == []

    def test_none_items_filtered(self):
        block = DepsBlock(requires=["a", None, "b"])
        assert block.requires == ["a", "b"]


# ===================================================================
# BasicCard
# ===================================================================


class TestBasicCard:
    def test_valid_title_case(self):
        card = BasicCard(Front="What is X?", Back="X is Y.")
        assert card.Front == "What is X?"
        assert card.Back == "X is Y."
        assert card.model == "Basic"

    def test_valid_lower_case(self):
        card = BasicCard.model_validate({"front": "Q?", "back": "A."})
        assert card.Front == "Q?"
        assert card.Back == "A."

    def test_missing_front_raises(self):
        with pytest.raises(ValidationError, match="Front"):
            BasicCard(Front="", Back="A.")

    def test_missing_back_raises(self):
        with pytest.raises(ValidationError, match="Back"):
            BasicCard(Front="Q?", Back="")

    def test_missing_both_raises(self):
        with pytest.raises(ValidationError):
            BasicCard(Front="", Back="")

    def test_whitespace_only_front_raises(self):
        with pytest.raises(ValidationError, match="Front"):
            BasicCard.model_validate({"Front": "   ", "Back": "A."})

    def test_default_model(self):
        card = BasicCard(Front="Q?", Back="A.")
        assert card.model == "Basic"

    def test_optional_fields(self):
        card = BasicCard(
            Front="Q?",
            Back="A.",
            id="arete_abc123",
            deck="Test::Deck",
            tags=["tag1"],
            markdown=False,
        )
        assert card.id == "arete_abc123"
        assert card.deck == "Test::Deck"
        assert card.tags == ["tag1"]
        assert card.markdown is False

    def test_anki_block(self):
        card = BasicCard.model_validate({
            "Front": "Q?",
            "Back": "A.",
            "anki": {"nid": "123", "cid": "456"},
        })
        assert card.anki is not None
        assert card.anki.nid == "123"
        assert card.anki.cid == "456"

    def test_deps_block(self):
        card = BasicCard.model_validate({
            "Front": "Q?",
            "Back": "A.",
            "deps": {"requires": ["dep1"], "related": ["rel1"]},
        })
        assert card.deps is not None
        assert card.deps.requires == ["dep1"]

    def test_extra_fields_allowed(self):
        card = BasicCard.model_validate({
            "Front": "Q?",
            "Back": "A.",
            "custom_field": "value",
        })
        assert card.model_extra is not None
        assert card.model_extra.get("custom_field") == "value"


# ===================================================================
# ClozeCard
# ===================================================================


class TestClozeCard:
    def test_valid(self):
        card = ClozeCard(Text="{{c1::answer}} is the key.")
        assert card.Text == "{{c1::answer}} is the key."
        assert card.model == "Cloze"

    def test_valid_lower_case(self):
        card = ClozeCard.model_validate({"text": "{{c1::X}} works."})
        assert card.Text == "{{c1::X}} works."

    def test_missing_text_raises(self):
        with pytest.raises(ValidationError, match="Text"):
            ClozeCard(Text="")

    def test_back_extra_alias_back_extra(self):
        card = ClozeCard.model_validate({
            "Text": "{{c1::X}}",
            "Back Extra": "Some extra info",
        })
        assert card.Back_Extra == "Some extra info"

    def test_back_extra_alias_extra(self):
        card = ClozeCard.model_validate({
            "Text": "{{c1::X}}",
            "Extra": "Some extra info",
        })
        assert card.Back_Extra == "Some extra info"

    def test_back_extra_alias_lower(self):
        card = ClozeCard.model_validate({
            "text": "{{c1::X}}",
            "extra": "Some extra",
        })
        assert card.Back_Extra == "Some extra"

    def test_back_extra_none_by_default(self):
        card = ClozeCard(Text="{{c1::X}}")
        assert card.Back_Extra is None


# ===================================================================
# CustomCard
# ===================================================================


class TestCustomCard:
    def test_valid(self):
        card = CustomCard.model_validate({
            "model": "Vocabulary",
            "Term": "hello",
            "Definition": "a greeting",
        })
        assert card.model == "Vocabulary"
        assert card.content_fields == {"Term": "hello", "Definition": "a greeting"}

    def test_reserved_fields_excluded_from_content(self):
        card = CustomCard.model_validate({
            "model": "Vocabulary",
            "Term": "hello",
            "id": "arete_abc",
            "deck": "Test",
            "tags": ["tag"],
            "markdown": True,
        })
        content = card.content_fields
        assert "Term" in content
        assert "id" not in content
        assert "deck" not in content
        assert "tags" not in content
        assert "markdown" not in content

    def test_no_content_fields(self):
        card = CustomCard(model="Empty")
        assert card.content_fields == {}

    def test_dunder_fields_excluded_from_content(self):
        card = CustomCard.model_validate({
            "model": "Custom",
            "__line__": 42,
            "Field1": "value",
        })
        assert "__line__" not in card.content_fields
        assert "Field1" in card.content_fields


# ===================================================================
# AreteFileMetadata
# ===================================================================


class TestAreteFileMetadata:
    def test_minimal(self):
        meta = AreteFileMetadata()
        assert meta.arete is False
        assert meta.deck is None
        assert meta.model == "Basic"
        assert meta.tags == []
        assert meta.cards == []

    def test_arete_true_with_deck(self):
        meta = AreteFileMetadata(arete=True, deck="Test::Deck", cards=[{"Front": "Q", "Back": "A"}])
        assert meta.arete is True
        assert meta.deck == "Test::Deck"

    def test_arete_true_without_deck_raises(self):
        with pytest.raises(ValidationError, match="deck"):
            AreteFileMetadata(arete=True, cards=[{"Front": "Q", "Back": "A"}])

    def test_cards_without_deck_raises(self):
        with pytest.raises(ValidationError, match="deck"):
            AreteFileMetadata(cards=[{"Front": "Q", "Back": "A"}])

    def test_tags_from_string(self):
        meta = AreteFileMetadata(tags="single_tag")
        assert meta.tags == ["single_tag"]

    def test_tags_from_list(self):
        meta = AreteFileMetadata(tags=["a", "b", "c"])
        assert meta.tags == ["a", "b", "c"]

    def test_tags_none_becomes_empty(self):
        meta = AreteFileMetadata(tags=None)
        assert meta.tags == []

    def test_tags_whitespace_filtered(self):
        meta = AreteFileMetadata(tags=["a", "", "  ", "b"])
        assert meta.tags == ["a", "b"]

    def test_extra_fields_allowed(self):
        meta = AreteFileMetadata.model_validate({
            "deck": "Test",
            "cards": [{"Front": "Q", "Back": "A"}],
            "custom_meta": "value",
        })
        assert meta.model_extra is not None
        assert meta.model_extra.get("custom_meta") == "value"

    def test_no_cards_no_deck_ok(self):
        """When there are no cards and arete is false, no deck is fine."""
        meta = AreteFileMetadata(arete=False, deck=None, cards=[])
        assert meta.deck is None


# ===================================================================
# parse_card dispatch
# ===================================================================


class TestParseCard:
    def test_basic_dispatch(self):
        card = parse_card({"Front": "Q?", "Back": "A."})
        assert isinstance(card, BasicCard)

    def test_basic_explicit_model(self):
        card = parse_card({"model": "Basic", "Front": "Q?", "Back": "A."})
        assert isinstance(card, BasicCard)

    def test_cloze_dispatch(self):
        card = parse_card({"model": "Cloze", "Text": "{{c1::X}}"})
        assert isinstance(card, ClozeCard)

    def test_cloze_case_insensitive(self):
        card = parse_card({"model": "cloze", "text": "{{c1::X}}"})
        assert isinstance(card, ClozeCard)

    def test_custom_dispatch(self):
        card = parse_card({"model": "Vocabulary", "Term": "hello", "Definition": "greeting"})
        assert isinstance(card, CustomCard)

    def test_default_model_override(self):
        card = parse_card({"Text": "{{c1::X}}"}, default_model="Cloze")
        assert isinstance(card, ClozeCard)

    def test_invalid_basic_raises(self):
        with pytest.raises(ValidationError):
            parse_card({"Front": "", "Back": "A."})

    def test_invalid_cloze_raises(self):
        with pytest.raises(ValidationError):
            parse_card({"model": "Cloze", "Text": ""})

    def test_model_field_preserved(self):
        card = parse_card({"model": "Cloze", "Text": "{{c1::X}}"})
        assert card.model == "Cloze"


# ===================================================================
# parse_file_metadata
# ===================================================================


class TestParseFileMetadata:
    def test_valid(self):
        meta = parse_file_metadata({
            "arete": True,
            "deck": "AI::Deep Learning",
            "model": "Basic",
            "tags": ["dl", "research"],
            "cards": [{"Front": "Q?", "Back": "A."}],
        })
        assert meta.deck == "AI::Deep Learning"
        assert meta.tags == ["dl", "research"]

    def test_invalid_raises(self):
        with pytest.raises(ValidationError):
            parse_file_metadata({"arete": True, "cards": [{"Front": "Q"}]})

    def test_empty_dict(self):
        meta = parse_file_metadata({})
        assert meta.arete is False
        assert meta.cards == []


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def test_none_front_treated_as_empty(self):
        with pytest.raises(ValidationError):
            BasicCard.model_validate({"Front": None, "Back": "A."})

    def test_numeric_front_coerced(self):
        """Numeric values should be coerced to strings via str()."""
        card = BasicCard.model_validate({"Front": 42, "Back": "A."})
        assert card.Front == "42"

    def test_numeric_text_coerced(self):
        card = ClozeCard.model_validate({"Text": 42})
        assert card.Text == "42"

    def test_basic_card_with_all_optional(self):
        card = BasicCard.model_validate({
            "Front": "Q?",
            "Back": "A.",
            "id": "arete_test123",
            "deck": "Test::Sub",
            "tags": ["t1"],
            "anki": {"nid": "n1", "cid": "c1"},
            "deps": {"requires": ["dep1"], "related": []},
            "markdown": False,
        })
        assert card.id == "arete_test123"
        assert card.deck == "Test::Sub"
        assert card.anki is not None
        assert card.anki.nid == "n1"
        assert card.deps is not None
        assert card.deps.requires == ["dep1"]
        assert card.markdown is False

