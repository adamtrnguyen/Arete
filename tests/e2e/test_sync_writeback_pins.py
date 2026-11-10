"""Golden pins: the ids each backend hands back for the vault, per kind of sync.

`UpdateItem.new_nid` and `new_cid` are what the pipeline writes into `anki.nid` and
`anki.cid`. One work item goes through the path the pipeline uses for the backend (the
shared `NoteSync` over the adapter for the backends in `SHARED`, the adapter's own
`sync_notes` for the others) against an empty collection (direct, add-on) or the fake,
and the reply is pinned against what Anki then holds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AnkiNote, UpdateItem, WorkItem
from tests.e2e.sync_harness import BACKENDS, Backend, make_backend

ID = "arete_01J00000000000000000000009"
SHARED = {"direct"}  # the backends the pipeline syncs through NoteSync


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
        if backend.name in SHARED:
            [result] = await NoteSync(bridge).sync_notes([item])  # type: ignore[arg-type]
        else:
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
    assert result.new_cid == _first_cid(backend, note.nid)


@pytest.mark.asyncio
async def test_create_cloze_names_the_card_of_ord_0(backend: Backend):
    """The cid is the first card by `ord`, which on a cloze note is not the lowest id."""
    item = _item(model="Cloze", Text="<p>{{c1::a}} {{c2::b}}</p>", **{"Back Extra": ""})
    result = await _sync(backend, item)

    [note] = backend.notes()
    assert len(note.cards) == 2
    assert note.cards[0].ord == 0
    assert result.ok and result.new_nid == str(note.nid)
    assert result.new_cid == str(note.cards[0].cid)


@pytest.mark.asyncio
async def test_update(backend: Backend):
    nid = backend.add_note("Basic", "Pins", {"Front": "old", "Back": "old"}, [ID])

    result = await _sync(backend, _item(nid))

    assert [n.nid for n in backend.notes()] == [nid]
    assert (result.ok, result.error, result.new_nid, result.new_cid) == (True, None, str(nid), None)


@pytest.mark.asyncio
async def test_no_nid_but_the_note_exists(backend: Backend):
    """Every backend heals onto the note with the id tag and reports its first card."""
    nid = backend.add_note("Basic", "Elsewhere", {"Front": "<p>Q pinned</p>", "Back": "x"}, [ID])

    result = await _sync(backend, _item())

    assert result.ok and result.error is None
    assert [n.nid for n in backend.notes()] == [nid]
    assert (result.new_nid, result.new_cid) == (str(nid), _first_cid(backend, nid))


@pytest.mark.asyncio
async def test_a_nid_anki_does_not_hold(backend: Backend):
    """The same heal when the vault's nid is dangling and the note exists by its id."""
    nid = backend.add_note("Basic", "Pins", {"Front": "<p>Q pinned</p>", "Back": "x"}, [ID])

    result = await _sync(backend, _item(1111111111111))

    assert result.ok
    assert [n.nid for n in backend.notes()] == [nid]
    assert (result.new_nid, result.new_cid) == (str(nid), _first_cid(backend, nid))


@pytest.mark.asyncio
async def test_a_malformed_nid_fails_the_card_and_writes_nothing(backend: Backend):
    result = await _sync(backend, _item("not-a-number"))

    assert (result.ok, result.new_nid, result.new_cid) == (False, None, None)
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
