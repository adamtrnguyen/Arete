"""Tests for the vault check CLI command."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner
from yaml import YAMLError
from yaml.error import Mark

from arete.interface.cli import app

runner = CliRunner()


# --- Parametrized: write content -> run vault check -> assert exit code + output ---


@pytest.mark.parametrize(
    "content,exit_code,expected_output",
    [
        # Valid files
        ("---\ndeck: Default\ncards:\n  - Front: A\n    Back: B\n---\nContent", 0, "Valid arete file"),
        ("---\narete: true\ndeck: Default\ncards: [{Front: Q}]\n---\nContent", 0, "Valid arete file"),
        ("# Just Markdown", 0, "Valid arete file"),
        # YAML syntax errors
        ("---\ndeck: D\n  bad_indent: v\n---\n", 1, "Indentation Error"),
        ("---\nkey: : value\n---\n", 1, "Validation Failed"),
        # Missing cards
        ("---\ndeck: Default\n---\n", 1, "Missing 'cards' list"),
        ("---\nmodel: Basic\n---\n", 1, "Missing 'cards' list"),
        # Tab handling
        ("---\ndeck: Default\ncards:\n\t- Front: A\n\t  Back: B\n---\n", 1, "Tab Character Error"),
        # Duplicate keys
        ("---\ndeck: A\ndeck: B\n---\n", 1, "Duplicate Key Error"),
        # Card validation
        ("---\ncards: not_a_list\n---\n", 1, "Expected a list"),
        ("---\ncards:\n  - not_a_dict\n---\n", 1, "Expected a dictionary"),
        ("---\ncards:\n  - {}\n---\n", 1, "is empty"),
        ("---\ncards:\n  - Back: only back\n---\n", 1, "Missing 'Front'?"),
        ("---\ncards:\n  - Front: f1\n  - Back: b2\n---\n", 1, "missing 'Front' field"),
        ("---\ncards:\n  - Text: t1\n  - Back: b2\n---\n", 1, "missing 'Text' field"),
        # Missing deck with arete flag
        ("---\narete: true\ncards: []\n---\n", 1, "missing 'deck' field"),
        # Split card error
        ("---\narete: true\ndeck: D\ncards:\n- Front: Q\n- Back: A\n---\n", 1, "Split Card Error"),
    ],
    ids=[
        "valid_basic",
        "valid_minimal",
        "no_frontmatter",
        "yaml_indent_error",
        "yaml_scanner_error",
        "missing_cards_with_deck",
        "missing_cards_with_model",
        "tab_in_frontmatter",
        "duplicate_keys",
        "cards_not_list",
        "card_not_dict",
        "card_empty",
        "card_missing_front",
        "inconsistent_front",
        "inconsistent_text",
        "missing_deck",
        "split_card",
    ],
)
def test_check_file(tmp_path, content, exit_code, expected_output):
    """Test via new 'vault check' path."""
    f = tmp_path / "test.md"
    f.write_text(content, encoding="utf-8")
    result = runner.invoke(app, ["vault", "check", str(f)])
    assert result.exit_code == exit_code
    assert expected_output in result.stdout


# --- File not found ---


def test_check_file_not_found():
    result = runner.invoke(app, ["vault", "check", "nonexistent.md"])
    assert result.exit_code == 1
    assert "File not found" in result.stdout


def test_check_file_not_found_json():
    result = runner.invoke(app, ["vault", "check", "nonexistent.md", "--json"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert "File not found." in result.stdout


# --- JSON output ---


def test_check_file_json_output_failure(tmp_path):
    f = tmp_path / "bad.md"
    f.write_text("---\ndeck: D\n  bad: i\n---\n", encoding="utf-8")

    result = runner.invoke(app, ["vault", "check", str(f), "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert len(data["errors"]) > 0
    assert data["errors"][0]["line"] > 0


def test_check_file_json_output_success(tmp_path):
    f = tmp_path / "good.md"
    f.write_text("---\ndeck: D\ncards: []\n---\n", encoding="utf-8")

    result = runner.invoke(app, ["vault", "check", str(f), "--json"])
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["stats"]["deck"] == "D"


def test_check_file_valid_card_count(tmp_path):
    f = tmp_path / "valid.md"
    f.write_text(
        "---\ndeck: Default\ncards:\n  - Front: A\n    Back: B\n---\nContent", encoding="utf-8"
    )
    result = runner.invoke(app, ["vault", "check", str(f)])
    assert result.exit_code == 0
    assert "Cards: 1" in result.stdout


# --- Edge cases with mocking ---


def test_check_file_yaml_error_context(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\nfoo: bar\n---\n")

    err = YAMLError()
    err.problem_mark = Mark("name", 0, 0, 0, "", 0)  # type: ignore
    err.problem = "problem"  # type: ignore
    err.context = "context_info"  # type: ignore

    with patch("arete.application.utils.text.validate_frontmatter", side_effect=err):
        result = runner.invoke(app, ["vault", "check", str(f)])
        assert result.exit_code == 1
        assert "context_info" in result.stdout


def test_check_file_generic_exception(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("content")

    with patch(
        "arete.application.utils.text.validate_frontmatter",
        side_effect=Exception("Generic Error"),
    ):
        result = runner.invoke(app, ["vault", "check", str(f), "--json"])
        assert result.exit_code == 0
        assert "Generic Error" in result.stdout
