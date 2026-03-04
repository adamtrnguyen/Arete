"""Tests for vault fix and vault migrate CLI commands."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from arete.application.utils.text import parse_frontmatter
from arete.interface.cli import app

runner = CliRunner()


# --- Fix File (vault fix) ---


def test_fix_file_replaces_tabs(tmp_path):
    f = tmp_path / "broken.md"
    f.write_text("---\ndeck:\tTabbed\ncards: []\n---\n")

    result = runner.invoke(app, ["vault", "fix", str(f)])
    assert result.exit_code == 0
    assert "auto-fixed" in result.stdout
    assert "\t" not in f.read_text()


def test_fix_file_no_changes(tmp_path):
    f = tmp_path / "clean.md"
    f.write_text("---\nkey: val\n---\n")

    result = runner.invoke(app, ["vault", "fix", str(f)])
    assert result.exit_code == 0
    assert "No fixable issues found" in result.stdout


def test_fix_file_not_found():
    result = runner.invoke(app, ["vault", "fix", "nonexistent.md"])
    assert result.exit_code == 1
    assert "File not found" in result.stdout


# --- Migrate (vault migrate) ---


def test_migrate_assigns_ids(tmp_path: Path):
    md_file = tmp_path / "test.md"
    md_file.write_text(
        "---\narete: true\ncards:\n  - fields:\n      Front: Question\n      Back: Answer\n---\n"
    )

    result = runner.invoke(app, ["vault", "migrate", str(md_file)])
    assert result.exit_code == 0
    assert "Migrated" in result.output

    content = md_file.read_text()
    meta, _ = parse_frontmatter(content)
    assert "cards" in meta
    assert len(meta["cards"]) == 1
    assert "id" in meta["cards"][0]
    assert meta["cards"][0]["id"].startswith("arete_")


def test_migrate_dry_run(tmp_path: Path):
    md_file = tmp_path / "test.md"
    original_content = (
        "---\narete: true\ncards:\n  - fields:\n      Front: Q\n      Back: A\n---\n"
    )
    md_file.write_text(original_content)

    result = runner.invoke(app, ["vault", "migrate", "--dry-run", str(md_file)])
    assert result.exit_code == 0
    assert "[DRY RUN]" in result.output
    assert md_file.read_text() == original_content


def test_migrate_preserves_existing_ids(tmp_path: Path):
    md_file = tmp_path / "test.md"
    md_file.write_text(
        "---\narete: true\ncards:\n  - id: arete_EXISTING\n    fields:\n      Front: Q\n---\n"
    )

    runner.invoke(app, ["vault", "migrate", str(md_file)])

    content = md_file.read_text()
    meta, _ = parse_frontmatter(content)
    assert meta["cards"][0]["id"] == "arete_EXISTING"


def test_migrate_yaml_error(tmp_path):
    p = tmp_path / "fail.md"
    p.write_text("---\narete: true\ninvalid: [\n---\nBody", encoding="utf-8")

    result = runner.invoke(app, ["vault", "migrate", str(tmp_path), "-vv"])
    assert result.exit_code == 0


def test_migrate_skip(tmp_path):
    p = tmp_path / "skip.md"
    p.write_text("---\nother: true\n---\nBody", encoding="utf-8")

    result = runner.invoke(app, ["vault", "migrate", str(tmp_path), "-vvv"])
    assert result.exit_code == 0


def test_migrate_redundant_frontmatter(tmp_path):
    p = tmp_path / "redundant.md"
    p.write_text("---\narete: true\n---\n---\n Body\n", encoding="utf-8")

    result = runner.invoke(app, ["vault", "migrate", str(tmp_path)])
    assert result.exit_code == 0
    content = p.read_text(encoding="utf-8")
    assert content.count("---") == 2


@patch("arete.application.utils.fs.iter_markdown_files")
def test_migrate_legacy_version(mock_iter, tmp_path):
    f = tmp_path / "legacy.md"
    f.write_text("---\nanki_template_version: 1\n---\n")
    mock_iter.return_value = [f]

    result = runner.invoke(app, ["vault", "migrate", str(tmp_path)])
    assert result.exit_code == 0
    assert "Migrated" in result.stdout
    assert "arete: true" in f.read_text()


@patch("arete.application.utils.fs.iter_markdown_files")
def test_migrate_skip_no_flag(mock_iter, tmp_path):
    f = tmp_path / "plain.md"
    f.write_text("---\ntitle: Hello\n---\n")
    mock_iter.return_value = [f]

    result = runner.invoke(app, ["vault", "migrate", str(tmp_path), "-v"])
    assert result.exit_code == 0
    assert "title: Hello" in f.read_text()


@patch("arete.application.utils.fs.iter_markdown_files")
def test_migrate_auto_heal_split(mock_iter, tmp_path):
    f = tmp_path / "split.md"
    f.write_text("---\narete: true\ncards:\n- Front: Q\n- Back: A\n---\n")
    mock_iter.return_value = [f]

    runner.invoke(app, ["vault", "migrate", str(tmp_path)])

    content = f.read_text()
    assert "Front: Q" in content
    assert "Back: A" in content
