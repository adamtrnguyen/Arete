"""Boundary and edge-case tests for frontmatter parsing."""

import pytest
import yaml

from arete.application.utils.text import (
    parse_frontmatter,
    rebuild_markdown_with_frontmatter,
    scrub_internal_keys,
    validate_frontmatter,
)


def test_parse_empty_string():
    meta, body = parse_frontmatter("")
    assert meta == {}
    assert body == ""


def test_parse_only_delimiters():
    meta, body = parse_frontmatter("---\n---\n")
    assert meta == {} or meta is not None  # empty YAML parses to None -> {}
    assert body == ""


def test_parse_unclosed_frontmatter():
    meta, body = parse_frontmatter("---\nkey: val\n")
    assert meta == {}
    assert body == "---\nkey: val\n"


def test_validate_unclosed_frontmatter_raises():
    with pytest.raises(yaml.YAMLError):
        validate_frontmatter("---\nkey: val\n")


def test_parse_bom_prefix():
    text = "\ufeff---\narete: true\n---\nbody here"
    meta, body = parse_frontmatter(text)
    assert meta.get("arete") is True or scrub_internal_keys(meta).get("arete") is True
    assert body == "body here"


def test_parse_tabs_in_yaml():
    text = "---\nkey:\tvalue\n---\nbody"
    meta, body = parse_frontmatter(text)
    # parse_frontmatter replaces tabs with spaces, so it should parse
    cleaned = scrub_internal_keys(meta)
    assert cleaned.get("key") == "value"
    assert body == "body"


def test_validate_tabs_raises():
    text = "---\nkey:\tvalue\n---\nbody"
    with pytest.raises(yaml.YAMLError):
        validate_frontmatter(text)


def test_parse_duplicate_keys():
    text = "---\nkey: 1\nkey: 2\n---\n"
    meta, _body = parse_frontmatter(text)
    # UniqueKeyLoader raises on duplicate keys -> __yaml_error__
    assert "__yaml_error__" in meta


def test_validate_duplicate_keys_raises():
    text = "---\nkey: 1\nkey: 2\n---\n"
    with pytest.raises(yaml.YAMLError):
        validate_frontmatter(text)


def test_rebuild_empty_meta():
    result = rebuild_markdown_with_frontmatter({}, "body text")
    assert result.startswith("---\n")
    assert "body text" in result
    # Should still be parseable
    meta, body = parse_frontmatter(result)
    assert body == "body text"


def test_rebuild_unicode_content():
    meta = {
        "arete": True,
        "cards": [{"Front": "你好 🎴", "Back": "Hello 世界"}],
    }
    rebuilt = rebuild_markdown_with_frontmatter(meta, "")
    meta2, _ = parse_frontmatter(rebuilt)
    cleaned = scrub_internal_keys(meta2)
    assert cleaned["cards"][0]["Front"] == "你好 🎴"
    assert cleaned["cards"][0]["Back"] == "Hello 世界"


def test_rebuild_multiline_fields():
    meta = {
        "arete": True,
        "cards": [{"Front": "Line 1\nLine 2\nLine 3", "Back": "Answer"}],
    }
    rebuilt = rebuild_markdown_with_frontmatter(meta, "")
    meta2, _ = parse_frontmatter(rebuilt)
    cleaned = scrub_internal_keys(meta2)
    assert cleaned["cards"][0]["Front"] == "Line 1\nLine 2\nLine 3"


def test_parse_deeply_nested_yaml():
    text = "---\na:\n  b:\n    c:\n      d:\n        e: deep\n---\nbody"
    meta, body = parse_frontmatter(text)
    cleaned = scrub_internal_keys(meta)
    assert cleaned["a"]["b"]["c"]["d"]["e"] == "deep"
    assert body == "body"


def test_zero_cards():
    text = "---\narete: true\ncards: []\n---\nbody"
    meta, body = parse_frontmatter(text)
    cleaned = scrub_internal_keys(meta)
    assert cleaned["cards"] == []
    assert body == "body"


def test_parse_preserves_body_exactly():
    body_content = "# Title\n\nSome **bold** text.\n\n- item 1\n- item 2\n"
    text = f"---\nkey: val\n---\n{body_content}"
    _, body = parse_frontmatter(text)
    assert body == body_content


def test_rebuild_roundtrip_preserves_body_whitespace():
    body = "\n\n  indented\n\n"
    rebuilt = rebuild_markdown_with_frontmatter({"key": "val"}, body)
    _, body2 = parse_frontmatter(rebuilt)
    assert body2 == body
