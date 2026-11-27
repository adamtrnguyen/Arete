"""Regression tests for frontmatter validation line numbers, deck checks, and tabs."""

from pathlib import Path

from arete.application.utils.text import validate_frontmatter
from arete.application.validation import validate_arete_file


def test_validate_frontmatter_reports_accurate_file_line_numbers():
    md = "---\narete: true\ndeck: Math\ncards:\n  - id: arete_A\n    Front: Q1\n    Back: A1\n---\n"
    meta = validate_frontmatter(md)
    assert meta["__line__"] == 2
    assert meta["cards"][0]["__line__"] == 5


def test_validate_frontmatter_tab_error_reports_exact_line():
    import pytest
    import yaml

    md = "---\narete: true\ndeck: Math\ncards:\n\t- Front: Q\n---\n"
    with pytest.raises(yaml.YAMLError) as exc_info:
        validate_frontmatter(md)
    mark = getattr(exc_info.value, "problem_mark", None)
    assert mark is not None
    assert mark.line + 1 == 5


def test_validate_arete_file_deduplicates_deck_error_and_allows_per_card_decks(tmp_path: Path):
    no_deck = tmp_path / "no_deck.md"
    no_deck.write_text(
        "---\narete: true\ncards:\n  - Front: Q1\n    Back: A1\n---\n",
        encoding="utf-8",
    )
    res_no_deck = validate_arete_file(no_deck)
    deck_errors = [e for e in res_no_deck.errors if "deck" in e["message"].lower()]
    assert len(deck_errors) == 1

    per_card = tmp_path / "per_card.md"
    per_card.write_text(
        "---\narete: true\ncards:\n  - deck: Math\n    Front: Q1\n    Back: A1\n---\n",
        encoding="utf-8",
    )
    res_per_card = validate_arete_file(per_card)
    assert res_per_card.ok, res_per_card.errors


def test_validate_arete_file_enforces_model_specific_fields(tmp_path: Path):
    """Basic requires Front+Back, Cloze requires Text, custom models require a content field."""
    cases = [
        (
            "basic_no_back.md",
            "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n---\n",
            False,
            "Back",
        ),
        (
            "basic_empty_front.md",
            "---\narete: true\ndeck: D\ncards:\n  - Front: '   '\n    Back: A\n---\n",
            False,
            "Front",
        ),
        (
            "cloze_no_text.md",
            "---\narete: true\ndeck: D\nmodel: Cloze\ncards:\n  - Back Extra: note\n---\n",
            False,
            "Text",
        ),
        (
            "cloze_valid.md",
            "---\narete: true\ndeck: D\nmodel: Cloze\ncards:\n  - Text: '{{c1::ans}}'\n---\n",
            True,
            "",
        ),
        (
            "custom_meta_only.md",
            "---\narete: true\ndeck: D\nmodel: CustomType\ncards:\n  - id: arete_1\n    tags: [t]\n---\n",
            False,
            "no content fields",
        ),
        (
            "custom_valid.md",
            "---\narete: true\ndeck: D\nmodel: CustomType\ncards:\n  - Prompt: P\n    Response: R\n---\n",
            True,
            "",
        ),
    ]
    for name, content, expected_ok, needle in cases:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        res = validate_arete_file(p)
        assert res.ok is expected_ok, (name, res.errors)
        if needle:
            assert any(needle in e["message"] for e in res.errors), (name, res.errors)
