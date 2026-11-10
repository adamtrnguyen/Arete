"""`arete vault fix` and `arete vault format` against files in tmp_path."""

import pytest
from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()

TABBED = "---\narete: true\ndeck: Default\ncards:\n\t- Front: A\n\t  Back: B\n---\nbody\n"
TABBED_FIXED = "---\narete: true\ndeck: Default\ncards:\n- Front: A\n  Back: B\n---\nbody\n"
QUOTED = '---\narete: true\ndeck: D\ncards:\n  - Front: "A"\n    Back: "line1\\nline2"\n---\nbody\n'
QUOTED_FORMATTED = (
    "---\narete: true\ndeck: D\ncards:\n- Front: A\n  Back: |-\n    line1\n    line2\n---\nbody\n"
)
CLEAN = TABBED_FIXED


# --- fix ---


def test_fix_missing_file_exits_1(tmp_path):
    result = runner.invoke(app, ["vault", "fix", str(tmp_path / "nope.md")])
    assert result.exit_code == 1
    assert "File not found." in result.stdout


def test_fix_rewrites_the_file_and_says_so(tmp_path):
    note = tmp_path / "n.md"
    note.write_text(TABBED, encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert result.exit_code == 0
    assert "File auto-fixed!" in result.stdout
    assert note.read_text(encoding="utf-8") == TABBED_FIXED


def test_fix_leaves_a_clean_file_byte_identical(tmp_path):
    note = tmp_path / "n.md"
    note.write_text(CLEAN, encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert result.exit_code == 0
    assert "No fixable issues found." in result.stdout
    assert "cannot be auto-fixed" not in result.stdout
    assert note.read_text(encoding="utf-8") == CLEAN


def test_fix_second_run_is_a_no_op(tmp_path):
    note = tmp_path / "n.md"
    note.write_text(TABBED, encoding="utf-8")
    runner.invoke(app, ["vault", "fix", str(note)])
    again = runner.invoke(app, ["vault", "fix", str(note)])
    assert "No fixable issues found." in again.stdout
    assert note.read_text(encoding="utf-8") == TABBED_FIXED


@pytest.mark.xfail(
    strict=True,
    reason="vault fix on unparseable YAML it cannot change raises ScannerError from "
    "validate_frontmatter instead of printing the 'cannot be auto-fixed' note",
)
def test_fix_unfixable_yaml_reports_instead_of_crashing(tmp_path):
    note = tmp_path / "bad.md"
    note.write_text("---\nkey: : value\n---\n", encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert result.exit_code == 0
    assert "cannot be auto-fixed" in result.stdout


@pytest.mark.xfail(
    strict=True,
    reason="vault fix warns 'validation errors' for a note without frontmatter, "
    "which vault check accepts as valid",
)
def test_fix_plain_markdown_is_not_flagged_as_invalid(tmp_path):
    note = tmp_path / "plain.md"
    note.write_text("# Just Markdown\n", encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert result.exit_code == 0
    assert "cannot be auto-fixed" not in result.stdout


# --- format ---


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "quoted.md").write_text(QUOTED, encoding="utf-8")
    (v / "clean.md").write_text(CLEAN, encoding="utf-8")
    return v


def test_format_dry_run_reports_the_count_and_writes_nothing(vault):
    result = runner.invoke(app, ["vault", "format", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "[DRY RUN] Would have formatted 1 files." in result.stdout
    assert (vault / "quoted.md").read_text(encoding="utf-8") == QUOTED
    assert (vault / "clean.md").read_text(encoding="utf-8") == CLEAN


def test_format_normalises_block_scalars_and_skips_files_already_formatted(vault):
    result = runner.invoke(app, ["vault", "format", str(vault)])
    assert result.exit_code == 0
    assert f"Formatting vault: {vault.resolve()}" in result.stdout
    assert "Formatted 1 files." in result.stdout
    assert (vault / "quoted.md").read_text(encoding="utf-8") == QUOTED_FORMATTED
    assert (vault / "clean.md").read_text(encoding="utf-8") == CLEAN


def test_format_twice_formats_nothing_the_second_time(vault):
    runner.invoke(app, ["vault", "format", str(vault)])
    again = runner.invoke(app, ["vault", "format", str(vault)])
    assert "Formatted 0 files." in again.stdout
