"""`search_term`: a name put into an Anki search matches only itself.

What each escaped term matches in a real collection is pinned by the store contract
(`tests/e2e/test_note_store_contract.py`); this pins the text.
"""

import pytest

from arete.infrastructure.anki.search import search_term


@pytest.mark.parametrize(
    ("name", "term"),
    [
        ("Basic", '"note:Basic"'),
        ("My_Type", '"note:My\\_Type"'),
        ("Star*", '"note:Star\\*"'),
        ('Say "hi"', '"note:Say \\"hi\\""'),
        ("Back\\slash", '"note:Back\\\\slash"'),
        ("A::B (c):d", '"note:A::B (c):d"'),
    ],
)
def test_wildcards_quotes_and_backslashes_are_escaped(name: str, term: str):
    assert search_term("note", name) == term
