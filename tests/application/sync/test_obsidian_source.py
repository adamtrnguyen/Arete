import hashlib
from unittest.mock import MagicMock

from arete.application.sync.parser import MarkdownParser
from arete.application.utils.text import make_editor_note


def test_parser_adds_obsidian_source(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    parser = MarkdownParser(vault_root, media_dir)
    md_file = vault_root / "subdir" / "test.md"
    md_file.parent.mkdir()
    md_file.write_text("dummy")

    meta = {
        "deck": "Default",
        "cards": [
            {
                "model": "Basic",
                "Front": "Q",
                "Back": "A",
            }
        ],
    }

    mock_cache = MagicMock()
    mock_cache.get_hash.return_value = None

    notes, skipped, inventory = parser.parse_file(md_file, meta, mock_cache)

    assert len(notes) == 1
    note = notes[0]
    expected_source = "Vault|subdir/test.md|0|"
    assert note.fields["_obsidian_source"] == expected_source


def test_parser_extracts_correct_line_number(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    parser = MarkdownParser(vault_root, media_dir)
    md_file = vault_root / "test.md"

    # Create valid markdown with cards at specific lines
    # Line 1: ---
    # Line 2: arete: true
    # Line 3: deck: Default
    # Line 4: cards:
    # Line 5:   - model: Basic
    # Line 6:     Front: Q
    # Line 7:     Back: A
    # Line 8: ---
    content = (
        "---\narete: true\ndeck: Default\ncards:\n  - model: Basic\n    Front: Q\n    Back: A\n---"
    )
    md_file.write_text(content)

    from arete.application.utils.text import parse_frontmatter

    meta, _ = parse_frontmatter(content)

    mock_cache = MagicMock()
    mock_cache.get_hash.return_value = None

    notes, _, _ = parser.parse_file(md_file, meta, mock_cache)

    assert len(notes) == 1
    # The card starts at the '- model: Basic' line, which is line 5
    expected_source = "Vault|test.md|5|"
    assert notes[0].fields["_obsidian_source"] == expected_source
    assert notes[0].start_line == 5


def test_parser_ignores_cache_when_forced(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    # 1. Setup parser with ignore_cache=True
    parser = MarkdownParser(vault_root, media_dir, ignore_cache=True)
    md_file = vault_root / "test.md"
    content = (
        "---\narete: true\ndeck: Default\ncards:\n  - model: Basic\n    Front: Q\n    Back: A\n---"
    )
    md_file.write_text(content)

    from arete.application.utils.text import parse_frontmatter

    meta, _ = parse_frontmatter(content)

    # 2. Setup mock cache that returns a matching hash (simulating a cache hit)
    mock_cache = MagicMock()
    # Any hash will do, as long as it normally would cause a skip
    mock_cache.get_hash.return_value = "dummy_hash"

    # 3. Parse and verify that notes are NOT skipped
    notes, _, _ = parser.parse_file(md_file, meta, mock_cache)

    # Should be 1 note despite cache "hit" because ignore_cache=True
    assert len(notes) == 1
    assert "<p>Q</p>" in notes[0].fields["Front"]


def test_hashing_includes_obsidian_source():
    fields_without = {"Front": "Q", "Back": "A"}
    fields_with = {"Front": "Q", "Back": "A", "_obsidian_source": "v|p|1"}

    note_without = make_editor_note("Basic", "Deck", [], fields_without)
    note_with = make_editor_note("Basic", "Deck", [], fields_with)

    hash_without = hashlib.md5(note_without.encode("utf-8")).hexdigest()
    hash_with = hashlib.md5(note_with.encode("utf-8")).hexdigest()

    # Hashing MUST be different so that adding the source field triggers an update
    assert hash_without != hash_with
    assert "## _obsidian_source" in note_with
    assert "v|p|1" in note_with


def test_cloze_hashing_includes_obsidian_source():
    fields_without = {"Text": "Q", "Back Extra": "A"}
    fields_with = {"Text": "Q", "Back Extra": "A", "_obsidian_source": "v|p|1"}

    note_without = make_editor_note("Cloze", "Deck", [], fields_without)
    note_with = make_editor_note("Cloze", "Deck", [], fields_with)

    hash_without = hashlib.md5(note_without.encode("utf-8")).hexdigest()
    hash_with = hashlib.md5(note_with.encode("utf-8")).hexdigest()

    assert hash_without != hash_with
    assert "## _obsidian_source" in note_with


def test_parser_uses_posix_paths_for_relative_source(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    parser = MarkdownParser(vault_root, media_dir)
    # Create a nested file structure
    subdir = vault_root / "subdir" / "nested"
    subdir.mkdir(parents=True)
    md_file = subdir / "test.md"

    content = "---\ndeck: D\ncards:\n- model: Basic\n  Front: Q\n  Back: A\n---"
    md_file.write_text(content)

    from arete.application.utils.text import parse_frontmatter

    meta, _ = parse_frontmatter(content)

    mock_cache = MagicMock()
    mock_cache.get_hash.return_value = None

    notes, _, _ = parser.parse_file(md_file, meta, mock_cache)

    assert len(notes) == 1
    # Check that path separators are forward slashes even if test runs on Windows (pathlib might return backslashes there)
    # The source format is vault|path/to/file|line
    expected_path_suffix = "subdir/nested/test.md"
    assert expected_path_suffix in notes[0].fields["_obsidian_source"]
    assert "\\" not in notes[0].fields["_obsidian_source"].split("|")[1]


def test_parser_handles_file_outside_vault_root(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    parser = MarkdownParser(vault_root, media_dir)

    # File outside vault
    outside_file = tmp_path / "Outside" / "test.md"
    outside_file.parent.mkdir()
    outside_file.write_text("deck: D\n---\nFront: Q\nBack: A")

    # We need valid meta to pass frontmatter check inside parse_file loop (implicit in loop logic)
    # but parse_file loop gets cards from 'meta'.
    meta = {"deck": "D", "cards": [{"model": "Basic", "Front": "Q", "Back": "A", "__line__": 1}]}

    mock_cache = MagicMock()
    mock_cache.get_hash.return_value = None

    notes, _, _ = parser.parse_file(outside_file, meta, mock_cache)

    assert len(notes) == 1
    # Check that _obsidian_source is NOT present because relative_to failed
    assert "_obsidian_source" not in notes[0].fields
