"""`anki_tags` says which tags the anki library keeps on a note.

Anki normalises a note's tags on every save that changes the note, and one pass is not
always its last word (`:<tab>:` is stored as `::`, and the next edit makes that
`blank::blank`). The sync sends `anki_tags` of the vault's tags and compares through it,
so the pins:

- a note added with `anki_tags(x)` holds exactly that, and still does after an edit;
- a note that holds anything else (the raw strings `x`, in whatever state Anki's own passes
  left them) holds `anki_tags(x)` once a sync writes them, and still does after an edit;
- `anki_tags` is a fixed point of itself.

Each check uses a new collection in a temporary directory: Anki also bends a new tag
toward one already registered (its case, and for a `::` component of colons only, a
registered parent's spelling), which a fresh collection keeps out of the pin.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from anki.collection import Collection
from hypothesis import given, settings
from hypothesis import strategies as st

from arete.domain.identity import anki_tags


@contextmanager
def fresh_collection() -> Iterator[Collection]:
    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "c.anki2"))
        try:
            yield col
        finally:
            col.close()


def added(col: Collection, tags: list[str]) -> int:
    note = col.new_note(col.models.by_name("Basic"))  # type: ignore[arg-type]
    note["Front"] = "tag pin"
    note.tags = list(tags)
    col.add_note(note, col.decks.id("Default"))  # type: ignore[arg-type]
    return note.id


def tags_of(col: Collection, nid: int) -> list[str]:
    return list(col.get_note(nid).tags)  # type: ignore[arg-type]


def edited(col: Collection, nid: int) -> list[str]:
    """The tags after a field edit saves the note with its tags as read."""
    note = col.get_note(nid)  # type: ignore[arg-type]
    note["Back"] = note["Back"] + "x"
    note.tags = list(note.tags)
    col.update_note(note)
    return tags_of(col, nid)


def folded(tags: list[str]) -> list[str]:
    return sorted(t.casefold() for t in tags)


def retagged(col: Collection, nid: int, tags: list[str]) -> list[str]:
    """The tags after the note is saved holding `tags`, as a sync writes them."""
    note = col.get_note(nid)  # type: ignore[arg-type]
    note.tags = list(tags)
    col.update_note(note)
    return tags_of(col, nid)


def check(tags: list[str]) -> None:
    expected = anki_tags(tags)
    assert anki_tags(expected) == expected, "a fixed point"
    with fresh_collection() as col:
        sent = added(col, expected)
        assert folded(tags_of(col, sent)) == folded(expected), "kept as sent"
        assert folded(edited(col, sent)) == folded(expected), "and after an edit"
        # What Anki makes of raw strings by itself depends on how many saves it has had and on
        # the tags the collection already holds (`:<tab>::` beside a registered `:` ends as
        # `blank::blank::blank`, not `blank:::blank`). The sync does not rely on it: it writes
        # `anki_tags` over whatever the note holds, and that is what must stick.
        raw = added(col, tags)
        assert folded(retagged(col, raw, expected)) == folded(expected), "a sync's tags stick"
        assert folded(edited(col, raw)) == folded(expected), "and stay there"


CASES = [
    "Cognitive Science",
    "Vector Space of Polynomials",
    "double  space",
    " lead",
    "trail ",
    "tab\there",
    "nl\nhere",
    "\t",
    ":\t:",
    ": :\t:",
    "nbsp inside",
    " nbsp-edge ",
    "ideo　space",
    "Parent::Child",
    "Parent :: Spaced",
    "a::",
    "::b",
    "::",
    ":::",
    "a:::b",
    "a:: ::b",
    "   ",
    "",
    "ümlaut",
    "nul\x00x",
    'quote"d',
    "a,b",
    "arete_01J00000000000000000000777",
]


@pytest.mark.parametrize("tag", CASES)
def test_a_tag_is_kept_as_anki_tags_says(tag):
    check([tag])


@pytest.mark.parametrize(
    "tag", ["Cognitive Science", "double  space", "Parent::Child", "nbsp inside", "a,b"]
)
def test_an_ordinary_tag_sent_raw_is_stored_as_anki_tags_says_at_once(tag):
    """For such tags the sync's tags equal what Anki makes of the raw strings on the add."""
    with fresh_collection() as col:
        assert folded(tags_of(col, added(col, [tag]))) == folded(anki_tags([tag]))


def test_case_variants_are_one_tag():
    with fresh_collection() as col:
        nid = added(col, ["Science", "SCIENCE", "science"])
        assert len(tags_of(col, nid)) == len(anki_tags(["Science", "SCIENCE", "science"]))


_PIECE = st.text(alphabet=st.sampled_from("aZé \t 　 :,_̈\x85"), max_size=8)


@settings(max_examples=150, deadline=None)
@given(st.lists(_PIECE, max_size=4))
def test_any_tag_list_is_kept_as_anki_tags_says(tags):
    check(tags)
