from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arete.application.sync.parser import MarkdownParser


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

    # Check fields that caused the crash
    assert note.source_file == md_file
    assert note.source_index == 1
    assert len(notes) == 1
    # Parser now renders HTML. Markdown default behavior wraps text in <p>
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
            {"model": "Custom", "cid": "123"},  # Only special fields, no content
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

    mock_cache.get_note.return_value = ("hash", json.dumps(note_dict))

    meta = {"cards": [{"model": "Basic"}]}  # Dummy meta, logic skips parsing

    # is_fresh=False
    notes, skipped, inventory = parser.parse_file(Path("test.md"), meta, mock_cache, is_fresh=False)

    assert len(notes) == 1
    assert notes[0].nid == "123"
    assert len(skipped) == 0


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

    assert len(notes2) == 0  # Skipped due to cache hit


def test_parse_no_deck_set(parser_fixture):
    parser, vault = parser_fixture
    # No deck in global or card -> should use parser.default_deck ("Default")
    meta = {"cards": [{"model": "Basic", "Front": "F", "Back": "B"}]}
    notes, skipped, _ = parser.parse_file(vault / "test.md", meta, MagicMock())
    assert len(notes) == 1
    assert len(skipped) == 0
    assert notes[0].deck == "Default"


def test_parse_cache_save_fail(parser_fixture):
    parser, vault = parser_fixture
    mock_cache = MagicMock()
    mock_cache.set_note.side_effect = Exception("Write Fail")

    meta = {"deck": "Default", "cards": [{"model": "Basic", "Front": "F", "Back": "B"}]}
    notes, _, _ = parser.parse_file(vault / "test.md", meta, mock_cache)

    assert len(notes) == 1
    # Should catch exception and log warning (assert logic flows)
