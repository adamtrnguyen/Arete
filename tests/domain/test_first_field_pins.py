"""Golden pins: the first-field key the sync matches notes by content with.

A note whose first field gives the same key as a card's is taken for that card when the
card has no usable nid and its Arete id finds nothing (census 1a #7 in the shared-sync
design). The cases are what `first_field_key` returns, including the ones that look wrong:
`A<br>B` joins two words, and an image-only field gives the empty key, which never matches.
"""

import pytest

from arete.domain.identity import first_field_key

CASES = [
    ("Hello World", "hello world"),
    ("<b>Bold</b>", "bold"),
    ("{{c1::answer}} is {{c2::correct}}", "answer is correct"),
    ("<!-- comment -->\n<p>Text</p>", "text"),
    ("  lots   of    spaces  ", "lots of spaces"),
    ("Mixed: {{c1::cloze}} and <em>html</em>", "mixed: cloze and html"),
    ("{{c1::a::hint}}", "a::hint"),
    ("{{c10::ten}}", "ten"),
    ("}} stray", "stray"),
    ("a&nbsp;b", "a&nbsp;b"),
    ("x &lt; y", "x &lt; y"),
    ("x > y < z", "x > y < z"),
    ("A<br>B", "ab"),
    ("A<br/>\nB", "a b"),
    (" nbsp ", "nbsp"),
    ("Ä Ö ÉCOLE", "ä ö école"),
    ("\\(x^2\\)", "\\(x^2\\)"),
    ('<img src="a.png">', ""),
    (" <p> </p> ", ""),
    ("", ""),
    ("<<b>a>", "a>"),
]


@pytest.mark.parametrize(("raw", "key"), CASES, ids=[repr(c[0]) for c in CASES])
def test_first_field_key(raw: str, key: str):
    assert first_field_key(raw) == key
