"""Property-based tests for the markdown->HTML converter."""

from hypothesis import given, settings
from hypothesis import strategies as st

from arete.application.sync.converter import markdown_to_anki_html
from arete.application.utils.text import convert_math_to_tex_delimiters

safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=("\x00",)),
    min_size=1,
    max_size=200,
)


@given(text=safe_text)
@settings(max_examples=100)
def test_math_delimiters_idempotent(text):
    """convert(convert(text)) == convert(text)."""
    once = convert_math_to_tex_delimiters(text)
    twice = convert_math_to_tex_delimiters(once)
    assert twice == once


@given(inner=st.from_regex(r"[a-zA-Z0-9+\-*/^_]{1,50}", fullmatch=True))
@settings(max_examples=50)
def test_math_preserves_content_inline(inner):
    """Inner math content unchanged after inline dollar conversion."""
    text = f"${inner}$"
    result = convert_math_to_tex_delimiters(text)
    assert inner in result


@given(inner=st.from_regex(r"[a-zA-Z0-9+\-*/^_]{1,50}", fullmatch=True))
@settings(max_examples=50)
def test_math_preserves_content_display(inner):
    """Inner math content unchanged after display dollar conversion."""
    text = f"$${inner}$$"
    result = convert_math_to_tex_delimiters(text)
    assert inner in result


@given(text=safe_text)
@settings(max_examples=50)
def test_html_output_not_empty(text):
    """markdown_to_anki_html(text) never returns empty for non-empty input."""
    result = markdown_to_anki_html(text)
    assert len(result) > 0


# Require non-whitespace at both ends: markdown legitimately strips/normalizes
# leading/trailing/whitespace-only code content, so a verbatim-preservation
# property only holds for code that actually carries non-whitespace boundaries.
@given(code=st.from_regex(r"[a-zA-Z0-9_]([a-zA-Z0-9_ ]{0,28}[a-zA-Z0-9_])?", fullmatch=True))
@settings(max_examples=30)
def test_code_blocks_preserved(code):
    """Fenced code content appears verbatim in output."""
    text = f"```\n{code}\n```"
    result = markdown_to_anki_html(text)
    assert code in result


@given(inner=st.from_regex(r"[a-zA-Z0-9+\-*/^_]{1,30}", fullmatch=True))
@settings(max_examples=50)
def test_no_raw_dollar_signs_in_output(inner):
    """After conversion of $...$, no bare dollar delimiters remain."""
    text = f"Some text ${inner}$ more text"
    result = convert_math_to_tex_delimiters(text)
    # The original $...$ should be converted to \(...\)
    assert f"${inner}$" not in result
    assert f"\\({inner}\\)" in result
