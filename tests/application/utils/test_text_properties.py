"""Property-based tests for YAML frontmatter round-trip integrity."""

from hypothesis import given, settings
from hypothesis import strategies as st

from arete.application.utils.text import (
    parse_frontmatter,
    rebuild_markdown_with_frontmatter,
    scrub_internal_keys,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies for Arete-shaped metadata
# ---------------------------------------------------------------------------

# Filter out characters that YAML normalizes (control chars, unicode line/paragraph seps)
_yaml_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Cc", "Zl", "Zp"),
        blacklist_characters=("\x00", "\x85", "\ufeff"),
    ),
    min_size=1,
    max_size=200,
)

card_st = st.fixed_dictionaries(
    {
        "Front": _yaml_safe_text,
        "Back": _yaml_safe_text,
    },
    optional={
        "id": st.from_regex(r"arete_[A-Z0-9]{26}", fullmatch=True),
        "model": st.sampled_from(["Basic", "Cloze"]),
        "deck": st.text(
            alphabet=st.characters(blacklist_categories=("Cs", "Cc", "Zl", "Zp")),
            min_size=1,
            max_size=50,
        ).filter(lambda s: "\n" not in s and ":" not in s),
    },
)

meta_st = st.fixed_dictionaries(
    {
        "arete": st.just(True),
        "cards": st.lists(card_st, min_size=1, max_size=5),
    },
    optional={
        "deck": st.text(
            alphabet=st.characters(blacklist_categories=("Cs", "Cc", "Zl", "Zp")),
            min_size=1,
            max_size=50,
        ).filter(lambda s: "\n" not in s),
        "tags": st.lists(st.from_regex(r"[a-z_]{1,20}", fullmatch=True), max_size=3),
    },
)

body_st = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Cc", "Zl", "Zp"), blacklist_characters=("\x00",)
    ),
    min_size=0,
    max_size=500,
)


@given(meta=meta_st, body=body_st)
@settings(max_examples=100)
def test_roundtrip_simple_meta(meta, body):
    """parse(rebuild(meta, body)) recovers same meta+body."""
    rebuilt = rebuild_markdown_with_frontmatter(meta, body)
    meta2, body2 = parse_frontmatter(rebuilt)

    assert body2 == body
    cleaned_original = scrub_internal_keys(meta)
    cleaned_parsed = scrub_internal_keys(meta2)
    assert cleaned_parsed == cleaned_original


@given(meta=meta_st, body=body_st)
@settings(max_examples=100)
def test_roundtrip_with_cards(meta, body):
    """Round-trip preserves card list structure."""
    rebuilt = rebuild_markdown_with_frontmatter(meta, body)
    meta2, _ = parse_frontmatter(rebuilt)
    clean2 = scrub_internal_keys(meta2)

    assert "cards" in clean2
    assert len(clean2["cards"]) == len(meta["cards"])
    for orig, parsed in zip(meta["cards"], clean2["cards"], strict=False):
        assert parsed["Front"] == orig["Front"]
        assert parsed["Back"] == orig["Back"]
        if "id" in orig:
            assert parsed["id"] == orig["id"]


@given(meta=meta_st, body=body_st)
@settings(max_examples=50)
def test_roundtrip_strips_internal_keys(meta, body):
    """__line__ keys removed after rebuild, data otherwise identical."""
    rebuilt = rebuild_markdown_with_frontmatter(meta, body)
    meta2, _ = parse_frontmatter(rebuilt)

    # meta2 may have __line__ from UniqueKeyLoader
    scrubbed = scrub_internal_keys(meta2)

    def has_dunder(d):
        if isinstance(d, dict):
            return any(k.startswith("__") for k in d) or any(has_dunder(v) for v in d.values())
        if isinstance(d, list):
            return any(has_dunder(v) for v in d)
        return False

    assert not has_dunder(scrubbed)


@given(meta=meta_st, body=body_st)
@settings(max_examples=50)
def test_rebuild_always_valid_yaml(meta, body):
    """rebuild(meta, body) always starts with --- and has closing ---."""
    rebuilt = rebuild_markdown_with_frontmatter(meta, body)
    assert rebuilt.startswith("---\n")
    # The closing --- appears after the YAML dump
    lines = rebuilt.split("\n")
    # Find the second ---
    delimiters = [i for i, line in enumerate(lines) if line.strip() == "---"]
    assert len(delimiters) >= 2


@given(
    text=st.text(min_size=0, max_size=500).filter(
        lambda s: not s.lstrip("\ufeff").startswith("---")
    )
)
@settings(max_examples=50)
def test_parse_no_frontmatter(text):
    """Any string without --- prefix -> empty meta, full body returned."""
    meta, body = parse_frontmatter(text)
    assert meta == {}
    # Body should be the original text (possibly BOM-stripped)
    assert body == text.lstrip("\ufeff")


@given(
    data=st.recursive(
        st.one_of(st.integers(), st.text(max_size=20), st.booleans(), st.none()),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5),
        ),
        max_leaves=20,
    )
)
@settings(max_examples=100)
def test_scrub_idempotent(data):
    """scrub(scrub(x)) == scrub(x)."""
    once = scrub_internal_keys(data)
    twice = scrub_internal_keys(once)
    assert twice == once
