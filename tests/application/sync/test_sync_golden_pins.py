"""Golden pins: identity-bearing facts of what the parser sends to Anki for a card.

Each value here decides whether a card is re-sent, which note it is matched to, or where
Anki links back to. The values are literals read off today's code, so a refactor of the sync
logic cannot move one silently: change a pin only together with the behaviour, on purpose.
"""

from __future__ import annotations

import logging
import shutil
import unicodedata
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from arete.application.sync.parser import MarkdownParser
from arete.application.utils.media import build_filename_index
from arete.application.utils.text import card_content_hash, parse_frontmatter
from arete.domain.models import AnkiNote
from tests.e2e.addon_host import addon_get_obsidian_source

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "corpus"
ID = "arete_01J00000000000000000000001"


def _cold_cache() -> MagicMock:
    cache = MagicMock()
    cache.get_hash.return_value = None
    cache.get_note.return_value = None
    return cache


def _parse(vault: Path, rel: str, meta: dict[str, Any], default_deck: str = "Default"):
    """Parse one file's meta as a fresh file with no cache hit, as the pipeline does."""
    media = vault.parent / "media"
    media.mkdir(exist_ok=True)
    parser = MarkdownParser(vault, media, default_deck=default_deck)
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    index = build_filename_index(vault, logging.getLogger(__name__))
    return parser.parse_file(path, meta, _cold_cache(), index, True)


def _one(vault: Path, card: dict[str, Any], **file_meta: Any) -> AnkiNote:
    notes, skipped, _ = _parse(vault, "pins.md", {"deck": "Pins", **file_meta, "cards": [card]})
    assert not skipped
    [note] = notes
    return note


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Vault"
    v.mkdir()
    return v


# ---------------------------------------------------------------------------
# 1. The tag list sent for a card
# ---------------------------------------------------------------------------


def test_tags_are_file_tags_then_card_tags_then_the_id_first_occurrence_wins(vault: Path):
    card = {"id": ID, "tags": ["c", "a", ID, " d ", ""], "Front": "Q", "Back": "A"}
    note = _one(vault, card, tags=["b", "a", " b ", ""])
    assert note.tags == ["b", "a", "c", ID, "d"]
    assert note.arete_id == ID


def test_the_id_tag_comes_last_when_no_tag_names_it(vault: Path):
    note = _one(vault, {"id": ID, "tags": "solo", "Front": "Q", "Back": "A"}, tags=7)
    assert note.tags == ["7", "solo", ID]


def test_a_file_tag_that_looks_like_an_id_is_sent_before_the_card_id(vault: Path):
    """The AnkiConnect reconcile reads the first `arete_` tag (census 1a #5): this one."""
    note = _one(vault, {"id": ID, "Front": "Q", "Back": "A"}, tags=["arete_review"])
    assert note.tags == ["arete_review", ID]


def test_a_card_without_an_id_sends_no_id_tag(vault: Path):
    note = _one(vault, {"Front": "Q", "Back": "A"}, tags=["x"])
    assert note.tags == ["x"]
    assert note.arete_id is None


_tag = st.text(alphabet="ab _", max_size=3)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(file_tags=st.lists(_tag, max_size=5), card_tags=st.lists(_tag, max_size=5))
def test_tags_property_order_of_first_appearance(
    vault: Path, file_tags: list[str], card_tags: list[str]
):
    note = _one(
        vault, {"id": ID, "tags": card_tags, "Front": "Q", "Back": "A"}, tags=list(file_tags)
    )
    stripped = [t.strip() for t in [*file_tags, *card_tags] if t and t.strip()]
    assert note.tags == list(dict.fromkeys([*stripped, ID]))


# ---------------------------------------------------------------------------
# 2. content_hash: what decides whether a card is sent again
# ---------------------------------------------------------------------------

HASH_CASES = [
    (
        "basic",
        "Basic",
        "D",
        ["t"],
        {"Front": "Q", "Back": "A"},
        None,
        None,
        "00f3ca0eea45b4c8f42797de76b23d94",
    ),
    (
        "basic, nid",
        "Basic",
        "D",
        ["t"],
        {"Front": "Q", "Back": "A"},
        "123",
        None,
        "eadc24626d5e85803799b4cc743c8d6e",
    ),
    (
        "basic, nid and cid",
        "Basic",
        "D",
        ["t"],
        {"Front": "Q", "Back": "A"},
        "123",
        "456",
        "eadc24626d5e85803799b4cc743c8d6e",
    ),
    (
        "cid without nid",
        "Basic",
        "D",
        ["t"],
        {"Front": "Q", "Back": "A"},
        None,
        "456",
        "3236924988543537727d5a50135c18c3",
    ),
    (
        "field order",
        "Basic",
        "D",
        ["t"],
        {"Back": "A", "Front": "Q"},
        None,
        None,
        "00f3ca0eea45b4c8f42797de76b23d94",
    ),
    (
        "trailing space",
        "Basic",
        "D",
        ["t"],
        {"Front": "Q  ", "Back": "A"},
        None,
        None,
        "00f3ca0eea45b4c8f42797de76b23d94",
    ),
    (
        "tag order",
        "Basic",
        "D",
        ["u", "t"],
        {"Front": "Q", "Back": "A"},
        None,
        None,
        "48ff4f904f4dbc2c14899a578c6cd279",
    ),
    (
        "unicode",
        "Basic",
        "Δ::日本",
        [],
        {"Front": "Cramér", "Back": "😀"},
        None,
        None,
        "4d8b3a35251da27e0e74d7a09f13abda",
    ),
]


@pytest.mark.parametrize(
    ("model", "deck", "tags", "fields", "nid", "cid", "expected"),
    [c[1:] for c in HASH_CASES],
    ids=[c[0] for c in HASH_CASES],
)
def test_card_content_hash_literal(model, deck, tags, fields, nid, cid, expected):
    assert card_content_hash(model, deck, tags, fields, nid, cid) == expected


def test_a_cid_counts_only_without_a_nid():
    fields = {"Front": "Q", "Back": "A"}
    with_cid = card_content_hash("Basic", "D", [], fields, "1", "2")
    assert with_cid == card_content_hash("Basic", "D", [], fields, "1", None)
    assert card_content_hash("Basic", "D", [], fields, None, "2") != card_content_hash(
        "Basic", "D", [], fields, None, None
    )


# The corpus as the parser renders it, in a vault named "Vault". Each hash covers the
# rendered fields, `_obsidian_source` (vault, path, line, id), deck, model, tags and ids.
CORPUS_HASHES = {
    "Cramér accents.md#1": "b59faea42485246c9c2e4d36bdf94f71",
    "basic.md#1": "d0fd8b81cb6d00ef0b81675b410371ae",
    "basic_with_extra.md#1": "157e03fac9a9381c1fe416c14894ea78",
    "callout_and_code.md#1": "b27800d0c1a6b0aa93ef737c2678168c",
    "card_level_deck.md#1": "8a10e362ea205bc7afa0ca822cf971b0",
    "card_level_deck.md#2": "19b5b42a86586b18184fa02db15588e0",
    "cloze.md#1": "8bf4a2b306944dec3db54910888a69d9",
    "deps.md#1": "203bbc5eb7e9b18b0594ab7a565e4154",
    "deps.md#2": "57059d82ba8fd631ecd8ff96296cd2b5",
    "math.md#1": "a1706b5d38b76b8d0c419ed26b4f744e",
    "media.md#1": "656217fbc90716a84e40e073fdd35545",
    "multi_card.md#1": "59df02cdfd331a729703b92de7933a5d",
    "multi_card.md#2": "0517288c75005232bfb37253d7148f71",
    "multi_card.md#3": "69bf63c87a25c2d5ced33ba14e4e65e9",
}


def _corpus_notes(tmp_path: Path) -> dict[str, AnkiNote]:
    vault = tmp_path / "Vault"
    shutil.copytree(CORPUS, vault)
    out: dict[str, AnkiNote] = {}
    for path in sorted(vault.glob("*.md")):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        notes, skipped, _ = _parse(vault, path.name, meta)
        assert not skipped
        for note in notes:
            key = unicodedata.normalize("NFC", f"{path.name}#{note.source_index}")
            out[key] = note
    return out


def test_corpus_content_hashes(tmp_path: Path):
    hashes = {k: n.content_hash for k, n in _corpus_notes(tmp_path).items()}
    assert hashes == CORPUS_HASHES


def test_the_same_card_with_a_nid_hashes_differently(vault: Path):
    """The hash covers the nid, so writing the nid back changes the hash of an unchanged card."""
    card = {"id": ID, "Front": "Q", "Back": "A", "__line__": 5}
    without = _one(vault, dict(card)).content_hash
    with_nid = _one(vault, {**card, "anki": {"nid": "123", "cid": "456"}}).content_hash
    assert (without, with_nid) == (
        "9e442895982c54fcda53aa1f30762ae5",
        "c7d82aa70fbd31d2362ca18275d1efe7",
    )


# ---------------------------------------------------------------------------
# 3. The `_obsidian_source` field: written by the parser, read by the add-on
# ---------------------------------------------------------------------------

# (vault dir, file path, card __line__, card id) -> the field, and the add-on's reading of it
SOURCE_CASES = [
    ("Vault", "a.md", 5, ID, "Vault|a.md|5|" + ID, ("Vault", "a.md", 5, ID)),
    (
        "Obsidian Vault",
        "sub dir/Cramér.md",
        12,
        ID,
        "Obsidian Vault|sub dir/Cramér.md|12|" + ID,
        ("Obsidian Vault", "sub dir/Cramér.md", 12, ID),
    ),
    ("Vault", "a.md", None, None, "Vault|a.md|0|", ("Vault", "a.md", 0, "")),
    ("Vault", "pipe|name.md", 3, ID, "Vault|pipe|name.md|3|" + ID, None),
    ("Va|ult", "a.md", 3, ID, "Va|ult|a.md|3|" + ID, None),
]


@pytest.mark.parametrize(
    ("vault_name", "rel", "line", "card_id", "field", "_addon"),
    SOURCE_CASES,
    ids=[f"{c[0]}/{c[1]}" for c in SOURCE_CASES],
)
def test_the_parser_writes_obsidian_source(
    tmp_path: Path, vault_name, rel, line, card_id, field, _addon
):
    vault = tmp_path / vault_name
    vault.mkdir()
    card: dict[str, Any] = {"Front": "Q", "Back": "A"}
    if line is not None:
        card["__line__"] = line
    if card_id is not None:
        card["id"] = card_id
    notes, _, _ = _parse(vault, rel, {"deck": "D", "cards": [card]})
    assert notes[0].fields["_obsidian_source"] == field


@pytest.mark.parametrize(
    ("_vault", "_rel", "_line", "_id", "field", "addon"),
    SOURCE_CASES,
    ids=[f"{c[0]}/{c[1]}" for c in SOURCE_CASES],
)
def test_the_addon_reads_obsidian_source(_vault, _rel, _line, _id, field, addon):
    get_obsidian_source = addon_get_obsidian_source()
    assert get_obsidian_source({"Front": "Q", "_obsidian_source": field}) == addon


def test_the_addon_reads_a_non_numeric_line_as_1_and_ignores_surrounding_space():
    get_obsidian_source = addon_get_obsidian_source()
    assert get_obsidian_source({"_obsidian_source": " V|a.md|x|id \n"}) == ("V", "a.md", 1, "id")
    assert get_obsidian_source({"_obsidian_source": ""}) is None
    assert get_obsidian_source({"Front": "no source field"}) is None


# ---------------------------------------------------------------------------
# 6. Deck and note type: which wins, and the name sent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_meta", "card", "default_deck", "deck", "model"),
    [
        ({"deck": "File"}, {"deck": "Card"}, "Fallback", "Card", "Basic"),
        ({"deck": "File"}, {"deck": ""}, "Fallback", "File", "Basic"),
        ({"deck": "File"}, {"deck": None}, "Fallback", "File", "Basic"),
        ({"deck": ""}, {}, "Fallback", "Fallback", "Basic"),
        ({"deck": "File  "}, {}, "Fallback", "File", "Basic"),
        ({"deck": "File", "model": "Cloze"}, {"model": "Basic"}, "F", "File", "Basic"),
        ({"deck": "File", "model": "basic"}, {}, "F", "File", "basic"),
        ({"deck": "File", "model": "Basic with Extra"}, {}, "F", "File", "Basic with Extra"),
    ],
)
def test_deck_and_model_resolution(vault: Path, file_meta, card, default_deck, deck, model):
    card = {"Front": "Q", "Back": "A", "Text": "{{c1::x}}", **card}
    notes, skipped, _ = _parse(vault, "d.md", {**file_meta, "cards": [card]}, default_deck)
    assert not skipped
    assert (notes[0].deck, notes[0].model) == (deck, model)


def test_a_basic_card_sends_only_front_back_and_the_source(vault: Path):
    note = _one(vault, {"id": ID, "Front": "Q", "Back": "A", "Extra": "dropped"})
    assert sorted(note.fields) == ["Back", "Front", "_obsidian_source"]


def test_a_custom_model_sends_every_key_but_aretes_own(vault: Path):
    card = {
        "id": ID,
        "model": "Basic with Extra",
        "Front": "Q",
        "Back": "A",
        "Back Extra": "E",
        "deck": "X",
        "tags": ["t"],
        "deps": {"requires": []},
        "anki": {"nid": "1"},
        "__line__": 3,
    }
    note = _one(vault, card)
    assert sorted(note.fields) == ["Back", "Back Extra", "Front", "_obsidian_source"]
