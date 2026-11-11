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


def test_fix_unfixable_yaml_reports_instead_of_crashing(tmp_path):
    note = tmp_path / "bad.md"
    note.write_text("---\nkey: : value\n---\n", encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert result.exit_code == 0
    assert "cannot be auto-fixed" in result.stdout


def test_fix_unfixable_yaml_prints_both_lines_and_leaves_the_file_alone(tmp_path):
    note = tmp_path / "bad.md"
    text = "---\nkey: : value\n---\n"
    note.write_text(text, encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert "No fixable issues found." in result.stdout
    assert "still has validation errors that cannot be auto-fixed" in result.stdout
    assert note.read_text(encoding="utf-8") == text


def test_fix_plain_markdown_is_not_flagged_as_invalid(tmp_path):
    note = tmp_path / "plain.md"
    note.write_text("# Just Markdown\n", encoding="utf-8")
    result = runner.invoke(app, ["vault", "fix", str(note)])
    assert result.exit_code == 0
    assert "cannot be auto-fixed" not in result.stdout


@pytest.mark.parametrize(
    "text",
    [
        "# Just Markdown\n",
        "---\nkey: : value\n---\n",
        "---\narete: true\ndeck: D\ncards:\n  - Front: A\n    Back: B\n---\n",
    ],
    ids=["plain", "bad-yaml", "valid"],
)
def test_fix_and_check_agree_on_whether_a_note_is_valid(tmp_path, text):
    note = tmp_path / "n.md"
    note.write_text(text, encoding="utf-8")
    checked = runner.invoke(app, ["vault", "check", str(note)])
    fixed = runner.invoke(app, ["vault", "fix", str(note)])
    assert (checked.exit_code == 0) == ("cannot be auto-fixed" not in fixed.stdout)


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


# --- line endings: a rewrite keeps the file's own, on every OS ---


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_format_keeps_the_files_line_endings(tmp_path, eol):
    v = tmp_path / "vault"
    v.mkdir()
    note = v / "quoted.md"
    note.write_bytes(QUOTED.replace("\n", eol).encode("utf-8"))

    assert runner.invoke(app, ["vault", "format", str(v)]).exit_code == 0

    assert note.read_bytes() == QUOTED_FORMATTED.replace("\n", eol).encode("utf-8")


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_fix_keeps_the_files_line_endings(tmp_path, eol):
    note = tmp_path / "n.md"
    note.write_bytes(TABBED.replace("\n", eol).encode("utf-8"))

    assert runner.invoke(app, ["vault", "fix", str(note)]).exit_code == 0

    assert note.read_bytes() == TABBED_FIXED.replace("\n", eol).encode("utf-8")


def test_a_crlf_file_differing_only_in_line_endings_is_not_formatted(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    note = v / "clean.md"
    note.write_bytes(CLEAN.replace("\n", "\r\n").encode("utf-8"))

    result = runner.invoke(app, ["vault", "format", str(v)])

    assert "Formatted 0 files." in result.stdout
    assert note.read_bytes() == CLEAN.replace("\n", "\r\n").encode("utf-8")
