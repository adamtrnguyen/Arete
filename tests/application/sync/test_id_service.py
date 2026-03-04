"""Tests for Arete ID generation."""

from pathlib import Path

from arete.application.sync.id_service import assign_arete_ids
from arete.application.utils.text import parse_frontmatter


def test_assign_arete_ids(tmp_path: Path):
    """Test that IDs are assigned to cards that lack them."""
    md_content = """---
arete: true
deck: Test
cards:
  - model: Basic
    fields:
      Front: "Q1"
      Back: "A1"
  - id: existing_id
    model: Basic
    fields:
      Front: "Q2"
      Back: "A2"
---
"""
    file_path = tmp_path / "test.md"
    file_path.write_text(md_content)

    assigned = assign_arete_ids(tmp_path)
    assert assigned == 1

    # Verify content
    content = file_path.read_text()
    meta, _ = parse_frontmatter(content)
    cards = meta["cards"]

    assert "id" in cards[0]
    assert cards[0]["id"].startswith("arete_")
    assert cards[1]["id"] == "existing_id"


def test_assign_arete_ids_dry_run(tmp_path: Path):
    """Test dry run doesn't modify files."""
    md_content = """---
arete: true
cards:
  - model: Basic
    fields: {Front: "Q"}
---
"""
    file_path = tmp_path / "test.md"
    file_path.write_text(md_content)

    assigned = assign_arete_ids(tmp_path, dry_run=True)
    assert assigned == 1

    content = file_path.read_text()
    assert "id:" not in content
