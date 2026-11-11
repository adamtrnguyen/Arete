from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arete.application.sync.parser import MarkdownParser
from arete.domain.constants import RENDER_VERSION


@pytest.fixture
def mock_cache():
    cache = MagicMock()
    cache.get_hash.return_value = None  # No cache hit
    return cache


@pytest.fixture
def parser_fixture(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()
    parser = MarkdownParser(vault_root, media_dir)
    return parser, vault_root


def test_parse_simple_card(parser_fixture, mock_cache):
    parser, vault = parser_fixture
    md_file = vault / "test.md"

    # Minimal valid frontmatter
    meta = {
        "deck": "Default",
        "cards": [
            {
                "model": "Basic",
                "Front": "Question",
                "Back": "Answer",
            }
        ],
    }

    notes, skipped, inventory = parser.parse_file(md_file, meta, mock_cache)

    assert len(notes) == 1
    assert len(skipped) == 0
    note = notes[0]

    # The note keeps its source position
    assert note.source_file == md_file
    assert note.source_index == 1
    assert len(notes) == 1
    # The parser renders HTML; Markdown wraps a paragraph in <p>
    assert "<p>Question</p>" in notes[0].fields["Front"]


def test_parse_basic_missing_fields(parser_fixture):
    parser, _ = parser_fixture
    meta = {
        "cards": [
            {"model": "Basic", "Front": "Only Front"},
            {"model": "Basic", "Back": "Only Back"},
        ]
    }
    notes, skipped, inventory = parser.parse_file(Path("test.md"), meta, MagicMock())
    assert len(notes) == 0
    assert len(skipped) == 2


def test_parse_cloze_missing_text(parser_fixture):
    parser, _ = parser_fixture
    meta = {
        "cards": [
            {"model": "Cloze", "Extra": "Hint"},
        ]
    }
    notes, skipped, inventory = parser.parse_file(Path("test.md"), meta, MagicMock())
    assert len(notes) == 0
    assert len(skipped) == 1


def test_parse_custom_no_fields(parser_fixture):
    parser, _ = parser_fixture
    meta = {
        "cards": [
            {"model": "Custom", "id": "arete_X", "deps": {}},  # Only Arete keys, no content
        ]
    }
    notes, skipped, inventory = parser.parse_file(Path("test.md"), meta, MagicMock())
    assert len(notes) == 0
    assert len(skipped) == 1


def test_parse_exception_handling(parser_fixture):
    parser, _ = parser_fixture
    parser.logger = MagicMock()  # Mock the logger

    # Mock image transform to raise exception (happens inside the loop)
    with patch(
        "arete.application.sync.parser.transform_images_in_text", side_effect=Exception(" Boom ")
    ):
        meta = {"cards": [{"model": "Basic", "Front": "F", "Back": "B"}]}
        notes, skipped, inventory = parser.parse_file(Path("test.md"), meta, MagicMock())
        assert len(notes) == 0
        assert len(skipped) == 1
        # Should log error
        parser.logger.error.assert_called()


def test_parse_missing_fields(parser_fixture, mock_cache):
    parser, vault = parser_fixture
    md_file = vault / "bad.md"

    meta = {
        "cards": [
            {
                "model": "Basic",
                # Missing Front/Back
            }
        ]
    }

    notes, skipped, inventory = parser.parse_file(md_file, meta, mock_cache)
    assert len(notes) == 0
    assert len(skipped) == 1


def test_parse_hot_cache_hit(parser_fixture):
    parser, vault = parser_fixture
    mock_cache = MagicMock()
    # Mock return value: (hash, json_str)
    # is_fresh=False must be passed to parse_file

    note_dict = {
        "model": "Basic",
        "deck": "Default",
        "fields": {"Front": "F", "Back": "B"},
        "tags": [],
        "start_line": 1,
        "end_line": 1,
        "source_file": str(vault / "test.md"),
        "source_index": 1,
        "nid": "123",
        "cid": "456",
        "content_hash": "hash",
    }
    import json

    mock_cache.get_note.return_value = (
        "hash",
        json.dumps(
            {**note_dict, "render_version": RENDER_VERSION, "render_key": parser.render_key}
        ),
    )
    # The vault card names the same note: its rendering is taken from the cache.
    meta = {"cards": [{"model": "Basic", "anki": {"nid": "123", "cid": "456"}}]}

    # is_fresh=False
    notes, skipped, inventory = parser.parse_file(Path("test.md"), meta, mock_cache, is_fresh=False)

    # An unchanged card is yielded from its cached rendering, so it is compared with Anki;
    # it stays in the prune inventory.
    [note] = notes
    assert (note.fields, note.nid, note.cid, note.unchanged) == (
        {"Front": "F", "Back": "B"},
        "123",
        "456",
        True,
    )
    assert {"nid": "123", "deck": "Default"} in inventory
    assert len(skipped) == 0


@pytest.mark.parametrize(
    ("cached_extra", "card_anki"),
    [
        ({}, {"nid": "999", "cid": "456"}),  # the vault names another note
        ({"render_key": "another-root"}, {"nid": "123", "cid": "456"}),  # other root etc.
        ({"render_key": None}, {"nid": "123", "cid": "456"}),  # cached with no key
    ],
    ids=["other-nid", "other-render-key", "no-render-key"],
)
def test_a_cached_rendering_is_not_used_for_another_card_or_renderer(
    parser_fixture, cached_extra, card_anki
):
    parser, vault = parser_fixture
    cache = MagicMock()
    cache.get_hash.return_value = None
    cached = {
        "model": "Basic",
        "deck": "Default",
        "fields": {"Front": "STALE", "Back": "STALE"},
        "tags": [],
        "start_line": 1,
        "end_line": 1,
        "source_file": str(vault / "test.md"),
        "source_index": 1,
        "nid": "123",
        "cid": "456",
        "content_hash": "hash",
        "render_version": RENDER_VERSION,
        "render_key": parser.render_key,
        **cached_extra,
    }
    import json

    cache.get_note.return_value = ("hash", json.dumps(cached))
    meta = {"deck": "Default", "cards": [{"Front": "F", "Back": "B", "anki": card_anki}]}

    [note], _, _ = parser.parse_file(vault / "test.md", meta, cache, is_fresh=False)

    assert note.fields["Front"] == "<p>F</p>" and not note.unchanged


def test_parse_hot_cache_corruption(parser_fixture):
    parser, vault = parser_fixture
    mock_cache = MagicMock()
    # Return invalid JSON
    mock_cache.get_note.return_value = ("hash", "{invalid")

    meta = {"cards": [{"model": "Basic", "Front": "F", "Back": "B"}]}

    # Should fall back to parsing (which we need valid meta for)
    # But parse logic needs deck.
    meta["deck"] = "Default"

    notes, kept, inv = parser.parse_file(vault / "test.md", meta, mock_cache, is_fresh=False)
    assert len(notes) == 1  # fell back to parsing
    # Warning logged


def test_parse_deep_cache_hit(parser_fixture):
    parser, vault = parser_fixture
    mock_cache = MagicMock()

    # parse normally returns "content_hash" for note.
    # We mock cache.get_hash to return SAME hash.
    # We need to know what hash will be calculated.
    # Hash depends on content.

    meta = {"deck": "Default", "cards": [{"model": "Basic", "Front": "F", "Back": "B"}]}

    # We rely on cached_hash == content_hash logic
    # We configure mock_cache.get_hash to return dynamic value?
    # Or just mock it to return ANY matching string if we can predict it.

    # Easier: Mock make_editor_note or hashlib?
    # Or just run once to get hash, then run again with mocked cache return.

    # Run 1:
    notes1, _, _ = parser.parse_file(vault / "test.md", meta, MagicMock())
    hash1 = notes1[0].content_hash

    # Run 2:
    mock_cache.get_hash.return_value = hash1
    notes2, _, _ = parser.parse_file(vault / "test.md", meta, mock_cache)

    # The same hash labels the card unchanged; it is still yielded and compared.
    [note] = notes2
    assert note.unchanged and note.content_hash == hash1


def test_parse_no_deck_set(parser_fixture):
    parser, vault = parser_fixture
    # No deck in global or card -> should use parser.default_deck ("Default")
    meta = {"cards": [{"model": "Basic", "Front": "F", "Back": "B"}]}
    notes, skipped, _ = parser.parse_file(vault / "test.md", meta, MagicMock())
    assert len(notes) == 1
    assert len(skipped) == 0
    assert notes[0].deck == "Default"


def test_parse_duplicate_nid_is_dropped(parser_fixture, mock_cache):
    """Two cards sharing one nid is impossible in Anki; trust neither nid."""
    parser, vault = parser_fixture
    parser.logger = MagicMock()
    meta = {
        "deck": "Default",
        "cards": [
            {"model": "Basic", "Front": "Q1", "Back": "A1", "anki": {"nid": "111"}},
            {"model": "Basic", "Front": "Q2", "Back": "A2", "anki": {"nid": "111"}},
        ],
    }
    notes, skipped, _ = parser.parse_file(vault / "dup.md", meta, mock_cache)

    assert len(notes) == 2
    assert len(skipped) == 0
    # Both cards must drop the shared nid so they sync as new notes.
    assert notes[0].nid is None
    assert notes[1].nid is None
    parser.logger.warning.assert_called()


def test_parse_cid_equals_nid_is_preserved(parser_fixture, mock_cache):
    """nid==cid is the NORMAL state for single-card notes and must be trusted.

    Anki assigns a single-card note's first card id equal to the note id, so the
    two coincide for the large majority of real cards (Arete itself writes them
    back equal). Treating equality as fabrication would strip valid ids and
    re-create the note as a duplicate on the next sync.
    """
    parser, vault = parser_fixture
    parser.logger = MagicMock()
    meta = {
        "deck": "Default",
        "cards": [
            {"model": "Basic", "Front": "Q", "Back": "A", "anki": {"nid": "999", "cid": "999"}},
        ],
    }
    notes, skipped, _ = parser.parse_file(vault / "cidnid.md", meta, mock_cache)

    assert len(notes) == 1
    assert len(skipped) == 0
    assert notes[0].nid == "999"
    assert notes[0].cid == "999"
    parser.logger.warning.assert_not_called()


def test_parse_valid_ids_are_preserved(parser_fixture, mock_cache):
    """Normal, unique nids must be trusted untouched — including nid==cid.

    nid==cid (first card id == note id) is exactly what Arete writes back for
    single-card notes, so it is the dominant real-world case and must survive.
    """
    parser, vault = parser_fixture
    meta = {
        "deck": "Default",
        "cards": [
            {"model": "Basic", "Front": "Q1", "Back": "A1", "anki": {"nid": "111", "cid": "111"}},
            {"model": "Basic", "Front": "Q2", "Back": "A2", "anki": {"nid": "333"}},
        ],
    }
    notes, skipped, _ = parser.parse_file(vault / "ok.md", meta, mock_cache)

    assert len(skipped) == 0
    assert notes[0].nid == "111"
    assert notes[0].cid == "111"
    assert notes[1].nid == "333"


def test_find_duplicate_nids_helper():
    """Helper detects nids declared by more than one card (v2 anki block only)."""
    cards = [
        {"anki": {"nid": "111"}},
        {"anki": {"nid": "111"}},  # same id -> duplicate
        {"anki": {"nid": "222"}},  # unique
        {"nid": "111"},  # a card-level nid outside the anki block is ignored
        "not a dict",  # ignored
        {"anki": {}},  # no nid
    ]
    assert MarkdownParser._find_duplicate_nids(cards) == {"111"}


def test_parse_card_level_tags_merge_with_file_tags(parser_fixture, mock_cache):
    parser, vault = parser_fixture
    md_file = vault / "test.md"

    meta = {
        "deck": "Default",
        "tags": ["file_tag", "file_dupe"],
        "cards": [
            {
                "model": "Basic",
                "Front": "Q",
                "Back": "A",
                "id": "arete_01HANDBUILTVAULEXAMPLE",
                "tags": ["card_tag", "file_dupe"],
            }
        ],
    }

    notes, _, _ = parser.parse_file(md_file, meta, mock_cache)
    assert len(notes) == 1
    tags = notes[0].tags

    # file + card tags merge, dedupe, order preserved, arete id appended
    assert tags[0] == "file_tag"
    assert "card_tag" in tags
    assert tags.count("file_dupe") == 1
    assert any(t.startswith("arete_") for t in tags)


def test_custom_model_does_not_send_arete_keys_as_fields(tmp_path):
    """id/deps/anki/__line__ are never sent to Anki as fields, whatever the model."""
    import logging

    from arete.application.sync.parser import MarkdownParser
    from arete.application.utils.text import parse_frontmatter
    from arete.infrastructure.persistence.cache import ContentCache

    md = tmp_path / "vocab.md"
    md.write_text(
        "---\narete: true\ndeck: T\ncards:\n  - id: arete_01X\n    model: Basic with Extra\n"
        "    Front: gato\n    Back: cat\n    deps:\n      requires: [Other]\n"
        "    anki:\n      nid: '123'\n---\n"
    )
    meta, _ = parse_frontmatter(md.read_text())
    parser = MarkdownParser(
        tmp_path, tmp_path / "media", ignore_cache=True, logger=logging.getLogger("t")
    )
    notes, _, _ = parser.parse_file(md, meta, ContentCache(tmp_path / "c.db"))
    leaked = {"id", "deps", "anki", "__line__"} & set(notes[0].fields)
    assert not leaked
    assert {"Front", "Back"} <= set(notes[0].fields)


def test_the_render_key_changes_with_the_markdown_library(tmp_path, monkeypatch):
    """A render cached under one markdown version is not reused under another."""
    import arete.application.sync.parser as parser_module

    here = parser_module.MarkdownParser(tmp_path, tmp_path / "media").render_key
    monkeypatch.setattr(parser_module, "RENDERER_LIBRARY", "markdown 0.0.1")
    there = parser_module.MarkdownParser(tmp_path, tmp_path / "media").render_key

    assert here != there
