"""Math delimiter edge cases: what stays literal and what becomes MathJax."""

import pytest

from arete.application.sync.converter import markdown_to_anki_html


@pytest.mark.parametrize(
    "text",
    [
        "price $$ 5 and more",  # display opener with no closer
        "costs $ 5 and $ 6",  # a dollar followed by a space opens nothing
        "total $",  # dollar at the very end
        "a $b c",  # inline opener with no closer
        "a $b\nc$ d",  # inline math cannot span lines
        "$$a$",  # one closing dollar does not close a display block
    ],
)
def test_unmatched_dollars_are_left_as_literal_text(text):
    html = markdown_to_anki_html(text)
    assert "\\(" not in html
    assert "\\[" not in html
    for line in text.split("\n"):
        assert line.strip() in html


def test_math_inside_a_double_backtick_span_holding_a_backtick_stays_literal():
    html = markdown_to_anki_html("``a ` b $x$`` and $y$")
    assert "<code>a ` b $x$</code>" in html
    assert r"\(y\)" in html


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_an_unclosed_fence_keeps_every_later_dollar_literal(fence):
    html = markdown_to_anki_html(f"$a$ before\n{fence}\n$x$ in code\n$y$")
    assert r"\(a\) before" in html
    assert "$x$ in code" in html
    assert "$y$" in html
    assert r"\(x\)" not in html
    assert r"\(y\)" not in html


def test_a_fence_closed_by_a_longer_fence_ends_the_literal_region():
    html = markdown_to_anki_html("```\n$x$\n````\n$y$")
    assert "$x$" in html
    assert r"\(x\)" not in html
    assert r"\(y\)" in html


def test_a_fence_is_not_closed_by_the_other_fence_character():
    html = markdown_to_anki_html("```\n$x$\n~~~\n$y$")
    assert "$x$" in html
    assert "$y$" in html
    assert "\\(" not in html


def test_a_fence_marker_as_the_last_line_without_a_newline_is_kept():
    html = markdown_to_anki_html("text\n```")
    assert html == "<p>text\n```</p>"
