"""The libyaml fast path must give exactly what the pure-Python loader gives.

Every check runs the same text twice: once as shipped, once with the fast loader switched
off, and compares the results, the error type and text, and the mark that `arete vault
check` reports. A difference means a sync, a check or a write-back would change.
"""

from pathlib import Path

import pytest
import yaml

from arete.application.utils import text

REPO = Path(__file__).resolve().parents[3]
FIXTURE_ROOTS = [REPO / "tests/fixtures", REPO / "tests/mock_vault", REPO / "tests/integrity_test"]
FIXTURES = sorted(p for root in FIXTURE_ROOTS for p in root.rglob("*.md"))

needs_libyaml = pytest.mark.skipif(
    text._FastUniqueKeyLoader is None, reason="PyYAML was built without libyaml"
)

# Inputs libyaml reads differently from PyYAML. Each must still give PyYAML's answer.
DIVERGENT = {
    "hash after block header": "---\ndeck: |#-\n  Test\n---\n",
    "hash after chomping": "---\ntitle: |-#x\n---\n",
    "bom inside": "---\ntags:\n\ufeff- a\ncards: []\n---\n",
    "bom before key": "---\na: 1\n\ufeffb: 2\n---\n",
    "empty non-specific tag": "---\na: !\nb: ! #c\n---\n",
    "question mark in flow": "---\ntags: [what?]\n---\n",
    "question mark in flow map": "---\na: {q: why?}\n---\n",
    "multiline flow question mark": "---\na: [x,\n  y?z]\n---\n",
}

ORDINARY = {
    "duplicate key": "---\na: 1\na: 2\n---\n",
    "nested duplicate key": "---\ncards:\n- Front: x\n  Front: y\n---\n",
    "bad indent": "---\na: 1\n  b: 2\n---\n",
    "unclosed flow": "---\na: [1, 2\n---\n",
    "valid flow with quoted question": '---\ntags: ["what?", b]\n---\n',
    "question mark in block text": "---\ncards:\n- Front: What is it?\n  Back: [[Link]] {x}\n---\n",
}


def _outcome(fn, md: str):
    try:
        return ("ok", repr(fn(md)))
    except Exception as e:  # noqa: BLE001 - the error itself is the outcome under test
        mark = getattr(e, "problem_mark", None)
        return (
            "raise",
            type(e).__name__,
            str(e),
            getattr(e, "problem", None),
            getattr(e, "context", None),
            (mark.line, mark.column) if mark is not None else None,
        )


def _both_ways(md: str, monkeypatch) -> tuple[list, list]:
    fns = (text.parse_frontmatter, text.validate_frontmatter, text.apply_fixes)
    fast = [_outcome(fn, md) for fn in fns]
    with monkeypatch.context() as m:
        m.setattr(text, "_FastUniqueKeyLoader", None)
        slow = [_outcome(fn, md) for fn in fns]
    return fast, slow


@needs_libyaml
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: str(p.relative_to(REPO)))
def test_every_fixture_parses_identically(path: Path, monkeypatch):
    md = path.read_text(encoding="utf-8")
    fast, slow = _both_ways(md, monkeypatch)
    assert fast == slow


@needs_libyaml
@pytest.mark.parametrize("md", list(DIVERGENT.values()) + list(ORDINARY.values()))
def test_known_libyaml_differences_give_the_pure_python_answer(md: str, monkeypatch):
    fast, slow = _both_ways(md, monkeypatch)
    assert fast == slow


@needs_libyaml
@pytest.mark.parametrize("md", DIVERGENT.values(), ids=DIVERGENT.keys())
def test_libyaml_alone_would_differ_on_these(md: str):
    """Pins why the gate exists: without it each of these inputs changes outcome."""
    raw = md.split("---\n")[1]
    ungated = type("Ungated", (text._UniqueKeyConstructor, yaml.CSafeLoader), {})

    def run(loader):
        try:
            return ("ok", repr(yaml.load(raw, Loader=loader)))
        except yaml.YAMLError as e:
            return ("raise", type(e).__name__)

    assert run(ungated) != run(text.UniqueKeyLoader)


@needs_libyaml
def test_ordinary_frontmatter_never_reaches_the_pure_python_loader(monkeypatch):
    """The speed-up holds only while the common case stays on libyaml."""
    built = []

    class Counting(text.UniqueKeyLoader):
        def __init__(self, stream):
            built.append(stream)
            super().__init__(stream)

    monkeypatch.setattr(text, "UniqueKeyLoader", Counting)
    md = (
        "---\narete: true\ndeck: A::B\ntags: [t]\ncards:\n"
        "- id: arete_X\n  Front: What is $x^2$?\n  Back: |-\n    See [[Note]] {x}\n"
        "  anki:\n    nid: '1'\n---\nbody\n"
    )
    meta, _ = text.parse_frontmatter(md)
    assert meta["cards"][0]["Front"] == "What is $x^2$?"
    assert built == []

    text.parse_frontmatter(DIVERGENT["question mark in flow"])
    assert len(built) == 1
