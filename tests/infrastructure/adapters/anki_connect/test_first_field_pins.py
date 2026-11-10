"""Golden pins: the first-field key the AnkiConnect adapter matches notes by content with.

Two notes whose first fields normalize to the same key are taken for the same card when a
card has no usable nid (census 1a #7 in the shared-sync design). The cases below are what
`_normalize_field` returns today, including the ones that look wrong: `A<br>B` joins two
words, and an image-only field normalizes to the empty key, which never matches.
"""

import pytest

from arete.domain.identity import first_field_key
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

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
    assert AnkiConnectAdapter._normalize_field(raw) == key
