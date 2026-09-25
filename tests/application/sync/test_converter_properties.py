"""Property-based tests for the markdown->HTML converter."""

from hypothesis import given, settings
from hypothesis import strategies as st

from arete.application.sync.converter import markdown_to_anki_html

safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=("\x00",)),
    min_size=1,
    max_size=200,
)


# Whitespace-only input is an empty field, and Python-Markdown drops the control
# characters it uses as placeholders (\x02, \x03), so those legitimately give "".
visible_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=200
).filter(str.strip)


@given(text=visible_text)
@settings(max_examples=50)
def test_html_output_not_empty(text):
    """markdown_to_anki_html(text) never returns empty for input with visible text."""
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
