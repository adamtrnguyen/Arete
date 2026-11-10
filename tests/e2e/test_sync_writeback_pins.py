"""Golden pins: the ids each backend hands back for the vault, per kind of sync.

`UpdateItem.new_nid` and `new_cid` are what the pipeline writes into `anki.nid` and
`anki.cid`. Each backend's adapter is called directly with one work item, against an empty
collection (direct, add-on) or the fake, and the reply is pinned against what Anki then holds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arete.domain.models import AnkiNote, UpdateItem, WorkItem
from tests.e2e.sync_harness import BACKENDS, Backend, make_backend

ID = "arete_01J00000000000000000000009"
AC = {"fake", "addon"}  # the backends behind the AnkiConnect adapter


def _item(nid: int | str | None = None, model: str = "Basic", **fields: str) -> WorkItem:
    fields = fields or {"Front": "<p>Q pinned</p>", "Back": "<p>A</p>"}
    note = AnkiNote(
        model=model,
        deck="Pins",
        fields={**fields, "_obsidian_source": f"vault|pins.md|5|{ID}"},
        tags=["t", ID],
        start_line=5,
        end_line=5,
        source_file=Path("pins.md"),
        source_index=1,
        nid=None if nid is None else str(nid),
        arete_id=ID,
    )
    return WorkItem(note=note, source_file=note.source_file, source_index=1)


@pytest.fixture(params=BACKENDS)
def backend(request, tmp_path: Path):
    b = make_backend(request.param, tmp_path)
    yield b
    b.close()


async def _sync(backend: Backend, item: WorkItem) -> UpdateItem:
    bridge = backend.bridge()
    try:
        [result] = await bridge.sync_notes([item])
    finally:
        await bridge.close()
    return result


def _first_cid(backend: Backend, nid: int) -> str:
    return str(backend.note(nid).cards[0].cid)


@pytest.mark.asyncio
async def test_create(backend: Backend):
    result = await _sync(backend, _item())

    [note] = backend.notes()
    assert (result.ok, result.error, result.new_nid) == (True, None, str(note.nid))
    expected_cid = _first_cid(backend, note.nid) if backend.name in AC else None
    assert result.new_cid == expected_cid


@pytest.mark.asyncio
async def test_create_cloze_names_the_card_of_ord_0(backend: Backend):
    """Over AnkiConnect the cid is the first card by `ord`; the direct backend sends none."""
    item = _item(model="Cloze", Text="<p>{{c1::a}} {{c2::b}}</p>", **{"Back Extra": ""})
    result = await _sync(backend, item)

    [note] = backend.notes()
    assert len(note.cards) == 2
    assert note.cards[0].ord == 0
    assert result.ok and result.new_nid == str(note.nid)
    assert result.new_cid == (str(note.cards[0].cid) if backend.name in AC else None)


@pytest.mark.asyncio
async def test_update(backend: Backend):
    nid = backend.add_note("Basic", "Pins", {"Front": "old", "Back": "old"}, [ID])

    result = await _sync(backend, _item(nid))

    assert [n.nid for n in backend.notes()] == [nid]
    assert (result.ok, result.error, result.new_nid, result.new_cid) == (True, None, str(nid), None)


@pytest.mark.asyncio
async def test_no_nid_but_the_note_exists(backend: Backend):
    """AnkiConnect heals onto the note with the id tag; the direct backend adds a second one."""
    nid = backend.add_note("Basic", "Elsewhere", {"Front": "<p>Q pinned</p>", "Back": "x"}, [ID])

    result = await _sync(backend, _item())

    nids = [n.nid for n in backend.notes()]
    assert result.ok and result.error is None
    if backend.name in AC:
        assert nids == [nid]
        assert (result.new_nid, result.new_cid) == (str(nid), _first_cid(backend, nid))
    else:
        [new] = [n for n in nids if n != nid]
        assert (result.new_nid, result.new_cid) == (str(new), None)


@pytest.mark.asyncio
async def test_a_nid_anki_does_not_hold(backend: Backend):
    """Same split as above when the vault's nid is dangling and the note exists by its id."""
    nid = backend.add_note("Basic", "Pins", {"Front": "<p>Q pinned</p>", "Back": "x"}, [ID])

    result = await _sync(backend, _item(1111111111111))

    nids = [n.nid for n in backend.notes()]
    assert result.ok
    if backend.name in AC:
        assert nids == [nid]
        assert (result.new_nid, result.new_cid) == (str(nid), _first_cid(backend, nid))
    else:
        assert len(nids) == 2 and result.new_nid == str(max(nids)) and result.new_cid is None


@pytest.mark.asyncio
async def test_a_malformed_nid_fails_the_card_and_writes_nothing(backend: Backend):
    """The direct backend echoes the vault's nid in a failed reply; the pipeline ignores it."""
    result = await _sync(backend, _item("not-a-number"))

    echoed = "not-a-number" if backend.name == "direct" else None
    assert (result.ok, result.new_nid, result.new_cid) == (False, echoed, None)
    assert backend.notes() == []


@pytest.mark.asyncio
async def test_create_files_the_note_under_the_sent_deck_and_type(backend: Backend):
    item = _item(model="Basic with Extra", Front="<p>Q</p>", Back="<p>A</p>", **{"Back Extra": "E"})
    item.note.deck = "Pins::Nested Deck"

    result = await _sync(backend, item)

    [note] = backend.notes()
    assert result.ok
    assert (note.model, [c.deck for c in note.cards]) == ("Basic with Extra", ["Pins::Nested Deck"])
    assert note.fields["Back Extra"] == "E"
