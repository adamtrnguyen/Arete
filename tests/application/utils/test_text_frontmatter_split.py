"""The three frontmatter readers agree on where frontmatter starts and ends.

Expected values are what parse_frontmatter, validate_frontmatter and
_extract_frontmatter_bounds returned when each found the closing `---` itself.
"""

import pytest
import yaml

from arete.application.utils.text import (
    _extract_frontmatter_bounds,
    parse_frontmatter,
    validate_frontmatter,
)

CASES = [
    ("", ({}, ""), {}, None),
    ("---", ({}, "---"), yaml.scanner.ScannerError, None),
    ("---\n---\n", ({}, ""), {}, ("", 4, 4)),
    (
        "  ---\na: 1\n---\nbody",
        ({"a": 1, "__line__": 2}, "body"),
        {"a": 1, "__line__": 1},
        ("a: 1", 6, 10),
    ),
    (
        "---  \na: 1\n---   \nbody",
        ({"a": 1, "__line__": 2}, "body"),
        {"a": 1, "__line__": 1},
        ("a: 1", 6, 10),
    ),
    (
        "---\r\na: 1\r\n---\r\nb",
        ({"a": 1, "__line__": 2}, "b"),
        {"a": 1, "__line__": 1},
        ("a: 1\r", 5, 10),
    ),
    ("x\n---\na: 1\n---", ({}, "x\n---\na: 1\n---"), {}, None),
    (
        "---\na: 1\n ---\nb: 2\n---\nafter",
        ({"a": "1 ---", "b": 2, "__line__": 2}, "after"),
        {"a": "1 ---", "b": 2, "__line__": 1},
        ("a: 1\n ---\nb: 2", 4, 18),
    ),
]


@pytest.mark.parametrize(("md", "parsed", "validated", "bounds"), CASES)
def test_frontmatter_boundaries(md, parsed, validated, bounds):
    assert parse_frontmatter(md) == parsed
    if isinstance(validated, type):
        with pytest.raises(validated):
            validate_frontmatter(md)
    else:
        assert validate_frontmatter(md) == validated
    assert _extract_frontmatter_bounds(md) == bounds
