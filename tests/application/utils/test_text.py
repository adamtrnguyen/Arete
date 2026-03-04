"""Consolidated tests for arete.application.utils.text and arete.application.utils.common."""

import pytest
import yaml
import yaml.constructor
import yaml.scanner

from arete.application.utils.text import (
    apply_fixes,
    convert_math_to_tex_delimiters,
    fix_mathjax_escapes,
    make_editor_note,
    parse_frontmatter,
    rebuild_markdown_with_frontmatter,
    scrub_internal_keys,
    validate_frontmatter,
)


# ---------- Math Conversion Tests ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param(
            "Let $x=1$ and $y=2$.", r"Let \(x=1\) and \(y=2\).", id="inline_dollar"
        ),
        pytest.param("$$abc$$", r"\[abc\]", id="block_simple"),
        pytest.param(
            "BLOCK:\n$$\ncontent\n$$", "BLOCK:\n" r"\[" "content" r"\]", id="block_multiline"
        ),
        pytest.param(
            "The value is $x$ which is $$x^2$$.",
            r"The value is \(x\) which is \[x^2\].",
            id="combined",
        ),
        pytest.param(r"Cost is \$50.", r"Cost is \$50.", id="escaped_dollars"),
        pytest.param("Code: `x = $y$`", r"Code: `x = \(y\)`", id="code_block"),
    ],
)
def test_math_conversion(text, expected):
    assert convert_math_to_tex_delimiters(text) == expected


# ---------- Frontmatter Parsing Tests ----------


def test_parse_frontmatter_valid():
    md = "---\ntitle: Hello\ncards:\n  - Front: A\n---\nBody content"
    meta, body = parse_frontmatter(md)
    assert meta["title"] == "Hello"
    assert len(meta["cards"]) == 1
    assert body.strip() == "Body content"


def test_parse_frontmatter_empty():
    md = "Just text, no YAML."
    meta, body = parse_frontmatter(md)
    assert meta == {}
    assert body == md


def test_parse_frontmatter_invalid_yaml():
    md = "---\n: broken yaml\n---\nBody"
    meta, body = parse_frontmatter(md)
    assert "__yaml_error__" in meta
    assert body == md


def test_parse_frontmatter_tabs():
    """Tabs in frontmatter are replaced by spaces during parsing."""
    text = "---\n\tkey: value\n---\ncontent"
    meta, rest = parse_frontmatter(text)
    assert scrub_internal_keys(meta) == {"key": "value"}
    assert rest == "content"


# ---------- Frontmatter Validation Tests ----------


def test_validate_frontmatter_valid():
    content = "---\nfoo: bar\n---\nbody"
    meta = validate_frontmatter(content)
    assert meta["foo"] == "bar"


def test_validate_frontmatter_tabs():
    content = "---\nfoo:\tbar\n---\nbody"
    with pytest.raises(yaml.scanner.ScannerError) as exc:
        validate_frontmatter(content)
    assert "cannot start any token" in str(exc.value)


def test_validate_frontmatter_unclosed():
    content = "---\nfoo: bar\nbody"
    with pytest.raises(yaml.scanner.ScannerError) as exc:
        validate_frontmatter(content)
    assert "Unclosed YAML" in str(exc.value)


def test_validate_frontmatter_tabs_error():
    """Validate_frontmatter strictly raises ScannerError for tabs."""
    text = "---\nkey:\n\tvalue\n---\n"
    with pytest.raises(yaml.scanner.ScannerError) as exc:
        validate_frontmatter(text)
    assert "found character '\\t'" in str(exc.value)


def test_duplicate_keys_error():
    """Verify DuplicateKeyLoader logic via validate_frontmatter."""
    text = "---\nkey: v1\nkey: v2\n---\n"
    with pytest.raises(yaml.constructor.ConstructorError) as exc:
        validate_frontmatter(text)
    assert "found duplicate key 'key'" in str(exc.value)


# ---------- apply_fixes Tests ----------


def test_apply_fixes_tabs():
    raw = "---\nfoo:\tbar\n---\n"
    fixed = apply_fixes(raw)
    meta, _ = parse_frontmatter(fixed)
    assert meta["foo"] == "bar"


def test_apply_fixes_missing_cards():
    raw = "---\ndeck: Default\n---\n"
    fixed = apply_fixes(raw)
    meta, _ = parse_frontmatter(fixed)
    assert meta["cards"] == []
    assert meta["deck"] == "Default"


def test_apply_fixes_template_tags():
    raw = "---\ntitle: {{title}}\n---\n"
    fixed = apply_fixes(raw)
    meta, _ = parse_frontmatter(fixed)
    assert meta["title"] == "{{title}}"


def test_apply_fixes_indentation_nid():
    """Same-line nid gets split and associated with the card."""
    raw = "---\ncards:\n- Front: Q\n  nid: 123\n---\n"
    fixed = apply_fixes(raw)
    meta, _ = parse_frontmatter(fixed)
    assert meta["cards"][0]["nid"] == 123


def test_apply_fixes_multiline_quotes():
    """Double-quoted multiline YAML folds newlines to spaces (per YAML spec)."""
    raw = '---\nkey: "Line 1\n  Line 2"\n---\n'
    fixed = apply_fixes(raw)
    meta, _ = parse_frontmatter(fixed)
    assert "Line 1" in meta["key"]
    assert "Line 2" in meta["key"]


def test_apply_fixes_latex_round_trip():
    """LaTeX content survives parse → dump round-trip via block scalars."""
    raw = "---\nmath: |-\n  \\begin{equation}\n  E=mc^2\n  \\end{equation}\n---\n"
    fixed = apply_fixes(raw)
    meta, _ = parse_frontmatter(fixed)
    assert "\\begin{equation}" in meta["math"]
    assert "E=mc^2" in meta["math"]
    assert "\\end{equation}" in meta["math"]


def test_apply_fixes_preserves_body():
    """Body content after frontmatter is preserved."""
    raw = "---\ndeck: D\ncards: []\n---\n\n# My Note\n\nSome content here."
    fixed = apply_fixes(raw)
    assert "# My Note" in fixed
    assert "Some content here." in fixed


# ---------- Other Utils ----------


def test_fix_mathjax_escapes():
    raw = '---\nkey: "Some \\in set"\n---\n'
    fixed = fix_mathjax_escapes(raw)
    assert 'key: "Some \\\\in set"' in fixed


def test_rebuild_markdown_roundtrip():
    meta = {"nid": "123", "cards": []}
    body = "Original Body"
    rebuilt = rebuild_markdown_with_frontmatter(meta, body)

    parsed_meta, parsed_body = parse_frontmatter(rebuilt)
    assert parsed_meta["nid"] == "123"
    assert parsed_body.strip() == "Original Body"


def test_rebuild_markdown_format():
    meta = {"foo": "bar"}
    body = "Content"
    full_text = rebuild_markdown_with_frontmatter(meta, body)
    assert full_text.startswith("---\n")
    assert "foo: bar" in full_text
    assert "Content" in full_text


# ---------- make_editor_note Tests ----------


def test_make_editor_note_basic():
    note = make_editor_note(
        model="Basic",
        deck="MyDeck",
        tags=["t1", "t2"],
        fields={"Front": "Q", "Back": "A"},
        nid="999",
    )
    assert "nid: 999" in note
    assert "model: Basic" in note
    assert "deck: MyDeck" in note
    assert "tags: t1 t2" in note
    assert "## Front" in note
    assert "## Back" in note
    assert "Q" in note
    assert "A" in note


def test_make_editor_note_cloze():
    fields = {"Text": "cloze {{c1::test}}", "Back Extra": "extra", "Extra": "backup"}
    out = make_editor_note("Cloze", "deck", ["t1"], fields, nid="123")

    assert "nid: 123" in out
    assert "model: Cloze" in out
    assert "## Text" in out
    assert "cloze {{c1::test}}" in out
    assert "## Back Extra" in out
    assert "extra" in out


def test_make_editor_note_cid_only_no_nid():
    out = make_editor_note("Basic", "Default", [], {}, cid="999", nid=None)
    assert "cid: 999" in out
    assert "nid:" not in out


def test_make_editor_note_cloze_fallback_extra():
    """Test Cloze model fallback to 'Extra' if 'Back Extra' is missing."""
    fields = {"Text": "cloze", "Extra": "fallback_extra"}
    out = make_editor_note("Cloze", "deck", [], fields)
    assert "## Back Extra" in out
    assert "fallback_extra" in out
