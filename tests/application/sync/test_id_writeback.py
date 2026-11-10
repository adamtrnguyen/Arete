"""`write_card_ids`: a sync's id write changes the id lines of a note and no other byte."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arete.application.sync.id_writeback import CardIds, write_card_ids
from arete.application.utils.text import parse_frontmatter, scrub_internal_keys

ID_LINE = re.compile(r"^\s*(- )?(id|anki|nid|cid):")
NEW_ID = "arete_01J00000000000000000000NEW"


def _cards(text: str) -> list[dict[str, Any]]:
    meta, _ = parse_frontmatter(text)
    assert "__yaml_error__" not in meta, meta
    return scrub_internal_keys(meta)["cards"]


def _inserted_only(before: str, after: str) -> list[str]:
    """The lines `after` adds to `before`; fails unless `after` is `before` plus whole lines."""
    old, new = before.split("\n"), after.split("\n")
    added: list[str] = []
    i = 0
    for line in new:
        if i < len(old) and line == old[i]:
            i += 1
        else:
            added.append(line)
    assert i == len(old), "an original line was changed or dropped"
    return added


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def test_a_card_gets_its_id_and_anki_lines_at_its_own_indentation():
    text = "---\narete: true\ncards:\n    -   Front: a\n        Back: b\n---\nbody"
    out = write_card_ids(text, [CardIds(0, id=NEW_ID, nid="1", cid="2")])
    assert not out.refused
    assert _inserted_only(text, out.text) == [
        f"        id: {NEW_ID}",
        "        anki:",
        "          nid: '1'",
        "          cid: '2'",
    ]
    assert _cards(out.text) == [
        {"Front": "a", "Back": "b", "id": NEW_ID, "anki": {"nid": "1", "cid": "2"}}
    ]


def test_a_card_with_an_id_and_no_anki_gets_only_the_anki_lines():
    text = "---\ncards:\n- id: arete_A\n  Front: a\n---\n"
    out = write_card_ids(text, [CardIds(0, id="arete_A", nid="7")])
    assert _inserted_only(text, out.text) == ["  anki:", "    nid: '7'"]


def test_an_anki_mapping_with_only_nid_gets_cid_beside_it():
    text = "---\ncards:\n- Front: a\n  anki:\n      nid: '7'  # kept\n  Back: b\n---\n"
    out = write_card_ids(text, [CardIds(0, nid="7", cid="70")])
    assert _inserted_only(text, out.text) == ["      cid: '70'"]
    assert _cards(out.text)[0]["anki"] == {"nid": "7", "cid": "70"}


@pytest.mark.parametrize(
    ("held", "written"),
    [("'111'", "'222'"), ('"111"', '"222"'), ("111", "222"), ("", " '222'")],
    ids=["single", "double", "plain", "empty"],
)
def test_a_replaced_nid_keeps_its_quoting_and_its_comment(held: str, written: str):
    text = f"---\ncards:\n- Front: a\n  anki:\n    nid:{' ' if held else ''}{held}  # c\n---\n"
    out = write_card_ids(text, [CardIds(0, nid="222")])
    assert not out.refused
    sep = " " if held else ""
    assert out.text == text.replace(f"nid:{sep}{held}  # c", f"nid:{sep}{written}  # c")


def test_block_scalars_before_and_after_the_insertion_point_keep_their_values():
    text = (
        "---\ncards:\n- Front: |\n    one\n\n  Back: |+\n    kept\n\n\n"
        "- Front: >-\n    folded\n    text\n\n  # trailing comment\n"
        "- Back: b\n  Front: |2\n     indented by one\n---\n"
    )
    out = write_card_ids(text, [CardIds(i, id=f"arete_{i}", nid=str(i + 1)) for i in range(3)])
    assert not out.refused
    _inserted_only(text, out.text)
    assert [c["Front"] for c in _cards(out.text)] == [c["Front"] for c in _cards(text)]
    assert _cards(out.text)[0]["Back"] == "kept\n\n\n"


@pytest.mark.parametrize("header", ["|", "|+", ">", "|2"])
def test_a_block_scalar_that_ends_the_frontmatter_gets_the_ids_above_its_key(header: str):
    """With no line break after it, a line below would add one to an unstripped scalar."""
    text = f"---\ncards:\n- Front: a\n  Back: {header}\n    last\n---\nbody\n"
    out = write_card_ids(text, [CardIds(0, id=NEW_ID, nid="1")])
    assert _inserted_only(text, out.text) == [f"  id: {NEW_ID}", "  anki:", "    nid: '1'"]
    assert out.text.index("anki:") < out.text.index("Back:")
    assert _cards(out.text)[0]["Back"] == _cards(text)[0]["Back"]


ONLY_KEY = "its only key holds a block scalar that ends the frontmatter"


def test_a_card_whose_only_key_ends_the_frontmatter_in_a_block_scalar_is_refused():
    text = "---\ncards:\n- Front: |\n    last\n---\n"
    out = write_card_ids(text, [CardIds(0, id=NEW_ID)])
    assert out.text == text
    assert out.refused == {0: ONLY_KEY}


def test_a_flow_card_that_holds_its_ids_needs_no_edit():
    text = "---\ncards:\n- {id: arete_A, Front: a, anki: {nid: '1'}}\n---\n"
    out = write_card_ids(text, [CardIds(0, id="arete_A", nid="1")])
    assert (out.text, out.refused) == (text, {})


def test_crlf_and_a_missing_final_newline_are_kept():
    text = "---\r\ncards:\r\n- Front: a\r\n---\r\nlast line"
    out = write_card_ids(text, [CardIds(0, id=NEW_ID)])
    assert out.text == f"---\r\ncards:\r\n- Front: a\r\n  id: {NEW_ID}\r\n---\r\nlast line"


def test_a_bom_is_kept():
    text = "\ufeff---\ncards:\n- Front: a\n---\n"
    out = write_card_ids(text, [CardIds(0, id=NEW_ID)])
    assert out.text == f"\ufeff---\ncards:\n- Front: a\n  id: {NEW_ID}\n---\n"


def test_writing_the_ids_a_card_holds_changes_nothing():
    text = "---\ncards:\n- id: 'arete_A'\n  Front: a\n  anki: {nid: '1', cid: '2'}\n---\n"
    assert write_card_ids(text, [CardIds(0, id="arete_A", nid="1", cid="2")]).text == text


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("---\ncards:\n- {Front: a, Back: b}\n- Front: c\n---\n", "flow style"),
        ("---\ncards: [{Front: a}]\n---\n", "flow style"),
        ("---\ncards:\n- Front: a\n  anki: {nid: '1'}\n---\n", "not a block mapping"),
        ("---\ncards:\n- Front: a\n  anki: [1]\n---\n", "not a block mapping"),
        ("---\nbase: &b\n  Front: a\ncards:\n- *b\n---\n", "alias"),
        ("---\ncards:\n- just a string\n---\n", "not a mapping"),
        ("---\ncards:\n- Front: a\n  anki:\n    nid: |\n      1\n  Back: b\n---\n", "one-line"),
    ],
    ids=["flow-card", "flow-cards", "flow-anki", "list-anki", "alias", "string", "block-nid"],
)
def test_a_shape_that_cannot_be_edited_leaves_the_file_unchanged(text: str, why: str):
    out = write_card_ids(text, [CardIds(0, id=NEW_ID, nid="5")])
    assert out.text == text
    assert why in out.refused[0]


def test_one_refused_card_leaves_every_card_of_the_file_unwritten():
    text = "---\ncards:\n- Front: a\n- {Front: b}\n---\n"
    out = write_card_ids(text, [CardIds(0, id="arete_A"), CardIds(1, id="arete_B")])
    assert out.text == text
    assert list(out.refused) == [1]


def test_a_value_spanning_lines_is_not_replaced():
    text = "---\ncards:\n- Front: a\n  anki:\n    nid: '1\n      2'\n---\n"
    out = write_card_ids(text, [CardIds(0, nid="3")])
    assert out.text == text
    assert "spans several lines" in out.refused[0]


# ---------------------------------------------------------------------------
# Property: frontmatter as people write it
# ---------------------------------------------------------------------------

_word = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGH", min_size=1, max_size=6)
_free = st.text(alphabet="abcxyz ABC:#-'\"\\$[]{}", min_size=0, max_size=12)


@st.composite
def _content_lines(draw) -> list[str]:
    lines = draw(st.lists(st.lists(_word, min_size=1, max_size=3), min_size=1, max_size=3))
    out = [" ".join(ws) for ws in lines]
    if len(out) > 1 and draw(st.booleans()):
        out.insert(1, "")  # a blank line inside the scalar
    return out


@dataclass
class _Entry:
    key: str
    lines: list[str]  # the rows after `key:` as (suffix of the key row, then continuation rows)


@st.composite
def _value(draw, col: int) -> list[str]:
    """`["<text after 'key:'>", *continuation rows]` for a value at key column `col`."""
    kind = draw(st.sampled_from(["plain", "single", "double", "block", "flow"]))
    comment = draw(st.sampled_from(["", "  # note"]))
    if kind == "plain":
        return [" " + " ".join(draw(st.lists(_word, min_size=1, max_size=3))) + comment]
    if kind == "single":
        return [" '" + draw(_free).replace("'", "''") + "'" + comment]
    if kind == "double":
        return [" " + json.dumps(draw(_free)) + comment]
    if kind == "flow":
        return [" [" + ", ".join(draw(st.lists(_word, max_size=3))) + "]" + comment]
    style = draw(st.sampled_from(["|", ">"]))
    chomp = draw(st.sampled_from(["", "-", "+"]))
    indicator = draw(st.sampled_from(["", "2"]))
    step = 2 if indicator else draw(st.sampled_from([2, 4]))
    body = draw(_content_lines())
    pad = " " if indicator and draw(st.booleans()) else ""  # leading spaces kept by `|2`
    rows = [(" " * (col + step) + pad + line) if line else "" for line in body]
    rows += [""] * draw(st.integers(0, 2))  # trailing blank lines: kept only by `+`
    return [f" {style}{indicator}{chomp}{comment}", *rows]


@st.composite
def _card(draw, dash_col: int) -> tuple[list[str], dict[str, Any]]:
    gap = draw(st.integers(1, 3))
    col = dash_col + 1 + gap
    keys = draw(
        st.lists(
            st.sampled_from(["Front", "Back", '"Back Extra"', "Text", "tags", "deps"]),
            min_size=1,
            max_size=4,
            unique=True,
        )
    )
    entries = [_Entry(k, draw(_value(col))) for k in keys]
    if "deps" in keys:
        inner = " " * (col + 2)
        entries[keys.index("deps")] = _Entry("deps", ["", f"{inner}requires:", f"{inner}- x"])
    held: dict[str, Any] = {}
    if draw(st.booleans()):
        held["id"] = f"arete_{draw(_word)}"
        entries.insert(draw(st.integers(0, len(entries))), _Entry("id", [f" {held['id']}"]))
    anki = draw(st.sampled_from(["none", "nid", "both", "empty"]))
    if anki != "none":
        inner = " " * (col + draw(st.sampled_from([2, 4])))
        rows = [""]
        if anki in ("nid", "both"):
            held["nid"] = str(draw(st.integers(1, 10**13)))
            rows.append(f"{inner}nid: '{held['nid']}'")
        if anki == "both":
            held["cid"] = str(draw(st.integers(1, 10**13)))
            rows.append(f'{inner}cid: "{held["cid"]}"')
        entries.insert(draw(st.integers(0, len(entries))), _Entry("anki", rows))
    rows: list[str] = []
    for n, e in enumerate(entries):
        lead = " " * dash_col + "-" + " " * gap if n == 0 else " " * col
        rows.append(f"{lead}{e.key}:{e.lines[0]}")
        rows.extend(e.lines[1:])
        if draw(st.integers(0, 4)) == 0:
            rows.append(" " * col + "# a comment between keys")
    return rows, held


@st.composite
def _note(draw) -> tuple[str, list[dict[str, Any]]]:
    dash_col = draw(st.sampled_from([0, 2, 4]))
    rows = ["# owner's comment", "arete: true", "deck: 'D::E'   # deck"]
    if draw(st.booleans()):
        rows += ['aliases: [One, "Two"]', ""]
    rows.append("cards:")
    helds = []
    for _ in range(draw(st.integers(1, 4))):
        card_rows, held = draw(_card(dash_col))
        rows += card_rows
        helds.append(held)
        if draw(st.integers(0, 3)) == 0:
            rows.append("")
    if draw(st.booleans()):
        rows += ["# closing comment", "model: Basic"]
    eol = draw(st.sampled_from(["\n", "\r\n"]))
    body = draw(st.sampled_from(["", "Body.", "Body.\n", "# H\n\ntext"]))
    text = eol.join(["---", *rows, "---"]) + eol + body.replace("\n", eol)
    return text, helds


@st.composite
def _edits(draw, helds: list[dict[str, Any]]) -> list[CardIds]:
    """Per card: its own ids again, or new ones where it has none (inserts only)."""
    out = []
    for i, held in enumerate(helds):
        out.append(
            CardIds(
                i,
                id=held.get("id") or (f"arete_N{i}" if draw(st.booleans()) else None),
                nid=held.get("nid") or (str(5000 + i) if draw(st.booleans()) else None),
                cid=held.get("cid") or (str(9000 + i) if draw(st.booleans()) else None),
            )
        )
    return out


def _expected(text: str, edits: list[CardIds]) -> list[dict[str, Any]]:
    cards = _cards(text)
    for e in edits:
        if e.id:
            cards[e.index]["id"] = e.id
        if e.nid or e.cid:
            anki = cards[e.index].get("anki") or {}
            anki.update({k: v for k, v in (("nid", e.nid), ("cid", e.cid)) if v})
            cards[e.index]["anki"] = anki
    return cards


@settings(max_examples=300)
@given(data=st.data())
def test_removing_the_inserted_id_lines_gives_back_the_original_bytes(data):
    text, helds = data.draw(_note())
    assert "__yaml_error__" not in parse_frontmatter(text)[0], text
    edits = data.draw(_edits(helds))

    out = write_card_ids(text, edits)

    if out.refused:  # only the shape with no line to insert at: see the test above
        assert set(out.refused.values()) == {ONLY_KEY}, (out.refused, text)
        assert out.text == text
        return
    added = _inserted_only(text, out.text)
    assert all(ID_LINE.match(line) for line in added), added
    assert _cards(out.text) == _expected(text, edits)
    assert parse_frontmatter(out.text)[1] == parse_frontmatter(text)[1]
    assert write_card_ids(out.text, edits).text == out.text, "writing the same ids again"


@settings(max_examples=100)
@given(data=st.data())
def test_a_changed_nid_rewrites_only_its_own_line(data):
    text, helds = data.draw(_note())
    edits = [CardIds(i, nid="4242424242424242") for i, held in enumerate(helds) if "nid" in held]
    out = write_card_ids(text, edits)
    assert not out.refused
    old, new = text.split("\n"), out.text.split("\n")
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new, strict=True) if a != b]
    assert all(re.match(r"^\s*nid: '4242424242424242'\r?$", b) for _, b in changed), changed
    assert len(changed) == len(edits)
