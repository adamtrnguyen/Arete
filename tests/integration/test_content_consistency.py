from unittest.mock import MagicMock

import pytest

from arete.application.sync.parser import MarkdownParser


@pytest.fixture
def parser_fixture(tmp_path):
    vault_root = tmp_path / "Vault"
    vault_root.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    # Mock logger to avoid noise
    parser = MarkdownParser(vault_root, media_dir, logger=MagicMock())
    return parser, vault_root


def parse_card(parser, vault, frontmatter_cards):
    """Helper to parse a simulated file"""
    md_file = vault / "consistency.md"
    meta = {"cards": frontmatter_cards, "deck": "Default"}
    cache = MagicMock()
    cache.get_hash.return_value = None

    notes, _, _ = parser.parse_file(md_file, meta, cache)
    return notes[0] if notes else None


def test_consistency_basic_formatting(parser_fixture):
    """Verify standard Markdown elements (Bold, Italic, Code)"""
    parser, vault = parser_fixture

    card = {"model": "Basic", "Front": "This is **bold** and *italic*", "Back": "`code_inline`"}

    note = parse_card(parser, vault, [card])

    # Bold/Italic
    assert "<strong>bold</strong>" in note.fields["Front"]
    assert "<em>italic</em>" in note.fields["Front"]

    # Code
    assert "<code>code_inline</code>" in note.fields["Back"]


def test_consistency_math_blocks(parser_fixture):
    """Verify MathJax protection (the most critical consistency requirement)"""
    parser, vault = parser_fixture

    # Input uses Obsidian style ($, $$) or Anki style \(...)?
    # text.py utils convert $ -> \(

    card = {
        "model": "Basic",
        # Obsidian style input
        "Front": "Solve $x^2 = 4$",
        "Back": "$$ x = \\pm 2 $$",
    }

    note = parse_card(parser, vault, [card])

    # Check Front: Inline math
    # Expected: \( x^2 = 4 \) (normalized)
    assert r"\(x^2 = 4\)" in note.fields["Front"]

    # Check Back: Block math
    # Expected: \[ x = \pm 2 \]
    # Note: parser normalization strips newlines often or keeps them?
    # Let's check looseness.
    assert r"\[" in note.fields["Back"]
    assert r"\]" in note.fields["Back"]
    assert r"x = \pm 2" in note.fields["Back"]


def test_consistency_multiline_code(parser_fixture):
    """Verify fenced code blocks (should be pre/code tags)"""
    parser, vault = parser_fixture

    code_block = """
```python
def foo():
    return 1
```
"""
    card = {"model": "Basic", "Front": "Code:", "Back": code_block}

    note = parse_card(parser, vault, [card])

    assert "<pre><code" in note.fields["Back"]
    assert "def foo():" in note.fields["Back"]


def test_consistency_lists(parser_fixture):
    """Verify list parsing"""
    parser, vault = parser_fixture

    card = {"model": "Basic", "Front": "- Item 1\n- Item 2", "Back": "List"}

    note = parse_card(parser, vault, [card])

    assert "<ul>" in note.fields["Front"]
    assert "<li>Item 1</li>" in note.fields["Front"]


def test_consistency_tables(parser_fixture):
    """Verify table parsing extension"""
    parser, vault = parser_fixture

    table = """
| Header 1 | Header 2 |
| -------- | -------- |
| Cell 1   | Cell 2   |
"""
    card = {"model": "Basic", "Front": table, "Back": "Table"}

    note = parse_card(parser, vault, [card])

    assert "<table>" in note.fields["Front"]
    assert "<th>Header 1</th>" in note.fields["Front"]
    assert "<td>Cell 1</td>" in note.fields["Front"]
