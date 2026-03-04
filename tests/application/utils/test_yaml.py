"""Tests for arete.application.utils.yaml — YAML dumper with literal block scalars."""

import pytest
import yaml

from arete.application.utils.yaml import _LiteralDumper, _str_representer


# ---------- _LiteralDumper behavior ----------


def test_literal_dumper_multiline():
    """Multiline strings are dumped with |- block style."""
    data = {"key": "Line 1\nLine 2\nLine 3"}
    result = yaml.dump(data, Dumper=_LiteralDumper)
    assert "|-" in result or "|" in result
    assert "Line 1" in result
    assert "Line 2" in result


def test_literal_dumper_plain_string():
    """Plain strings without triggers are dumped normally."""
    data = {"name": "hello"}
    result = yaml.dump(data, Dumper=_LiteralDumper)
    assert "name: hello" in result
    # Should NOT have block style
    assert "|-" not in result


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("col1\tcol2", id="tab"),
        pytest.param("key: value", id="colon"),
        pytest.param("heading # comment", id="hash"),
        pytest.param("array [1, 2, 3]", id="brackets"),
        pytest.param("it's a test", id="single_quote"),
        pytest.param('He said "hello"', id="double_quote"),
        pytest.param("$E = mc^2$", id="dollar"),
        pytest.param("\\begin{equation}", id="backslash"),
        pytest.param("~null-like", id="tilde"),
    ],
)
def test_literal_dumper_trigger_characters(value):
    """Trigger characters cause block scalar or quoting; value survives round-trip."""
    data = {"key": value}
    result = yaml.dump(data, Dumper=_LiteralDumper)
    loaded = yaml.safe_load(result)
    assert loaded["key"] == value


def test_literal_dumper_trailing_newline_stripped():
    """Trailing newlines are stripped to produce |- (strip) style."""
    data = {"key": "content\n"}
    result = yaml.dump(data, Dumper=_LiteralDumper)
    # The dumper strips trailing newline, producing |- not |+
    assert "|-" in result
    assert "content" in result


def test_literal_dumper_preserves_internal_newlines():
    """Internal newlines are preserved in block scalar output."""
    data = {"key": "Line A\nLine B\nLine C"}
    result = yaml.dump(data, Dumper=_LiteralDumper)
    loaded = yaml.safe_load(result)
    assert "Line A" in loaded["key"]
    assert "Line B" in loaded["key"]
    assert "Line C" in loaded["key"]


def test_literal_dumper_no_aliases():
    """_LiteralDumper does not produce YAML aliases for repeated values."""
    shared = "repeated value"
    data = {"a": shared, "b": shared}
    result = yaml.dump(data, Dumper=_LiteralDumper)
    assert "*" not in result  # No alias markers
    assert "&" not in result  # No anchor markers


def test_literal_dumper_roundtrip_math():
    """LaTeX content survives dump → load round-trip."""
    original = {"formula": "\\frac{a}{b} + \\sum_{i=1}^{n}"}
    dumped = yaml.dump(original, Dumper=_LiteralDumper)
    loaded = yaml.safe_load(dumped)
    assert loaded["formula"] == original["formula"]


def test_literal_dumper_roundtrip_multiline():
    """Multiline block scalar content survives dump → load round-trip."""
    original = {"content": "First line\nSecond line\nThird line"}
    dumped = yaml.dump(original, Dumper=_LiteralDumper)
    loaded = yaml.safe_load(dumped)
    assert loaded["content"] == original["content"]


def test_literal_dumper_complex_structure():
    """Nested structures with mixed content types are correctly serialized."""
    data = {
        "cards": [
            {
                "Front": "What is $x$?",
                "Back": "Answer with \\frac{1}{2}\nand more",
            }
        ]
    }
    result = yaml.dump(data, Dumper=_LiteralDumper)
    loaded = yaml.safe_load(result)
    assert loaded["cards"][0]["Front"] == "What is $x$?"
    assert "\\frac{1}{2}" in loaded["cards"][0]["Back"]
    assert "and more" in loaded["cards"][0]["Back"]
