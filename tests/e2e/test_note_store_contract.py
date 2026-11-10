"""The `NoteStorePort` contract, one suite for every store that implements it.

The sync use-case decides against this port only, so each store must answer the same way:
the in-memory fake the use-case is tested with, and each adapter over a real collection.
`STORES` names the stores the suite runs on; each test seeds a collection the way a person
would in Anki, through the store's seeding helpers, then reads and writes through the port.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from arete.domain.interfaces import NoteStorePort, NoteStoreSession
from arete.domain.models import HeldNote, NewNote, NoteChange, OutcomeUnknown
from tests.fakes.note_store import ContractViolation, FakeNoteStore

ID = "arete_01J00000000000000000000101"
OTHER = "arete_01J00000000000000000000102"


@dataclass
class StoreUnderTest:
    """A store and the hands a test needs beside the port: seed a note, queue cards."""

    name: str
    store: NoteStorePort
    seed: Callable[..., int]  # (model, deck, fields, tags) -> nid
    queue: Callable[[list[int]], None]
    close: Callable[[], None]


def _fake(_root: Path) -> StoreUnderTest:
    fake = FakeNoteStore()
    return StoreUnderTest(
        "fake",
        fake,
        lambda model, deck, fields, tags: fake.add(model, deck, fields, tags),
        fake.queue,
        lambda: None,
    )


STORES: dict[str, Callable[[Path], StoreUnderTest]] = {"fake": _fake}


@pytest.fixture(params=sorted(STORES))
def sut(request, tmp_path: Path) -> Iterator[StoreUnderTest]:
    s = STORES[request.param](tmp_path)
    yield s
    s.close()


async def _read(sut: StoreUnderTest, nid: int) -> HeldNote:
    async with sut.store.session() as s:
        held = (await s.read_notes([nid]))[nid]
    assert isinstance(held, HeldNote)
    return held


def _tags(held: HeldNote) -> set[str]:
    return {t.casefold() for t in held.tags}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_notes_returns_the_note_in_field_and_card_order(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C::Deck", {"Front": "<p>Q</p>", "Back": "<p>A</p>"}, [ID, "t"])

    async with sut.store.session() as s:
        got = await s.read_notes([nid, 1111111111111])

    held = got[nid]
    assert isinstance(held, HeldNote)
    assert (held.nid, held.model) == (nid, "Basic")
    assert list(held.fields)[:2] == ["Front", "Back"]
    assert (held.fields["Front"], held.fields["Back"]) == ("<p>Q</p>", "<p>A</p>")
    assert _tags(held) == {ID.casefold(), "t"}
    assert [(c.ord, c.home_deck, c.in_filtered) for c in held.cards] == [(0, "C::Deck", False)]
    assert got[1111111111111] is None


@pytest.mark.asyncio
async def test_a_queued_card_reads_its_home_deck(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C::Home", {"Front": "Q", "Back": "A"}, [ID])
    sut.queue([nid])

    held = await _read(sut, nid)

    assert [(c.home_deck, c.in_filtered) for c in held.cards] == [("C::Home", True)]


@pytest.mark.asyncio
async def test_note_types_name_fields_cloze_and_templates(sut: StoreUnderTest):
    async with sut.store.session() as s:
        got = await s.note_types(["Basic", "Cloze", "No Such Type"])

    basic, cloze = got["Basic"], got["Cloze"]
    assert basic is not None and not isinstance(basic, Exception)
    assert cloze is not None and not isinstance(cloze, Exception)
    assert (basic.name, basic.fields[:2], basic.is_cloze, basic.n_templates) == (
        "Basic",
        ("Front", "Back"),
        False,
        1,
    )
    assert (cloze.fields[:2], cloze.is_cloze) == (("Text", "Back Extra"), True)
    assert got["No Such Type"] is None


@pytest.mark.asyncio
async def test_find_by_arete_ids_matches_the_whole_tag_in_any_case(sut: StoreUnderTest):
    older = sut.seed("Basic", "C", {"Front": "one", "Back": "x"}, [ID])
    newer = sut.seed(
        "Basic", "C", {"Front": "two", "Back": "x"}, [ID.upper().replace("ARETE", "arete")]
    )
    sut.seed("Basic", "C", {"Front": "three", "Back": "x"}, [OTHER, "arete_review"])

    async with sut.store.session() as s:
        got = await s.find_by_arete_ids([ID, "arete_01J00000000000000000000999"])

    found = got[ID]
    assert not isinstance(found, Exception)
    assert [h.nid for h in found] == [older, newer]
    assert got["arete_01J00000000000000000000999"] == []


@pytest.mark.asyncio
async def test_scan_model_lists_every_note_of_the_type(sut: StoreUnderTest):
    a = sut.seed("Basic", "C", {"Front": "one", "Back": "x"}, [])
    b = sut.seed("Basic", "D", {"Front": "two", "Back": "x"}, [ID])
    sut.seed("Cloze", "C", {"Text": "{{c1::x}}"}, [])

    async with sut.store.session() as s:
        scanned = await s.scan_model("Basic")

    assert sorted(h.nid for h in scanned) == [a, b]
    assert {h.fields["Front"] for h in scanned} == {"one", "two"}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_notes_adds_each_and_fails_only_the_bad_one(sut: StoreUnderTest):
    async with sut.store.session() as s:
        await s.create_decks(["C::New"])
        got = await s.create_notes(
            [
                NewNote("Basic", "C::New", {"Front": "Q", "Back": "A"}, (ID,)),
                NewNote("No Such Type", "C::New", {"Front": "Q"}, ()),
                NewNote("Cloze", "C::New", {"Text": "{{c1::a}} {{c2::b}}"}, (OTHER,)),
            ]
        )

    basic, missing, cloze = got
    assert isinstance(basic, int) and isinstance(cloze, int)
    assert isinstance(missing, Exception)
    held = await _read(sut, basic)
    assert (held.fields["Front"], _tags(held)) == ("Q", {ID.casefold()})
    assert [c.home_deck for c in held.cards] == ["C::New"]
    assert [c.ord for c in (await _read(sut, cloze)).cards] == [0, 1]


@pytest.mark.asyncio
async def test_a_duplicate_first_field_is_created_all_the_same(sut: StoreUnderTest):
    sut.seed("Basic", "C", {"Front": "Same", "Back": "one"}, [ID])

    async with sut.store.session() as s:
        [nid] = await s.create_notes(
            [NewNote("Basic", "C", {"Front": "Same", "Back": "two"}, (OTHER,))]
        )

    assert isinstance(nid, int)


@pytest.mark.asyncio
async def test_apply_changes_sets_every_facet(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C::Old", {"Front": "old", "Back": "old"}, [ID, "stale"])
    held = await _read(sut, nid)

    async with sut.store.session() as s:
        await s.create_decks(["C::New"])
        [error] = await s.apply_changes(
            [
                NoteChange(
                    nid,
                    fields={"Front": "new", "Back": "new"},
                    add_tags=("fresh",),
                    remove_tags=("stale",),
                    home_deck=(tuple(c.cid for c in held.cards), "C::New"),
                )
            ]
        )

    assert error is None
    after = await _read(sut, nid)
    assert (after.fields["Front"], after.fields["Back"]) == ("new", "new")
    assert _tags(after) == {ID.casefold(), "fresh"}
    assert [c.home_deck for c in after.cards] == ["C::New"]
    assert [c.cid for c in after.cards] == [c.cid for c in held.cards]


@pytest.mark.asyncio
async def test_retype_maps_the_fields_and_keeps_the_cards(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C", {"Front": "Q", "Back": "A"}, [ID])
    cids = [c.cid for c in (await _read(sut, nid)).cards]

    async with sut.store.session() as s:
        [error] = await s.apply_changes([NoteChange(nid, retype=("Basic with Extra", (0, 1, -1)))])

    assert error is None
    after = await _read(sut, nid)
    assert after.model == "Basic with Extra"
    assert (after.fields["Front"], after.fields["Back"], after.fields["Back Extra"]) == (
        "Q",
        "A",
        "",
    )
    assert [c.cid for c in after.cards] == cids


@pytest.mark.asyncio
async def test_a_queued_card_gets_a_new_home_deck_and_stays_queued(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C::Home", {"Front": "Q", "Back": "A"}, [ID])
    sut.queue([nid])
    held = await _read(sut, nid)

    async with sut.store.session() as s:
        await s.create_decks(["C::Moved"])
        [error] = await s.apply_changes(
            [NoteChange(nid, home_deck=(tuple(c.cid for c in held.cards), "C::Moved"))]
        )

    assert error is None
    assert [(c.home_deck, c.in_filtered) for c in (await _read(sut, nid)).cards] == [
        ("C::Moved", True)
    ]


@pytest.mark.asyncio
async def test_a_change_to_a_missing_note_fails_only_that_note(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C", {"Front": "Q", "Back": "A"}, [ID])

    async with sut.store.session() as s:
        missing, ok = await s.apply_changes(
            [
                NoteChange(1111111111111, fields={"Front": "x"}),
                NoteChange(nid, fields={"Front": "changed"}),
            ]
        )

    assert isinstance(missing, Exception) and ok is None
    assert (await _read(sut, nid)).fields["Front"] == "changed"


@pytest.mark.asyncio
async def test_add_field_appends_to_the_type_and_its_notes(sut: StoreUnderTest):
    nid = sut.seed("Basic", "C", {"Front": "Q", "Back": "A"}, [])

    async with sut.store.session() as s:
        await s.add_field("Basic", "_obsidian_source")
        await s.add_field("Basic", "_obsidian_source")
        info = (await s.note_types(["Basic"]))["Basic"]

    assert info is not None and not isinstance(info, Exception)
    assert info.fields.count("_obsidian_source") == 1
    assert (await _read(sut, nid)).fields["_obsidian_source"] == ""


@pytest.mark.asyncio
async def test_decks_are_listed_and_created(sut: StoreUnderTest):
    async with sut.store.session() as s:
        assert "C::Made" not in await s.deck_names()
        assert await s.create_decks(["C::Made"]) == {"C::Made": None}
        assert "C::Made" in await s.deck_names()


def test_the_port_has_no_way_to_delete():
    """Invariant 6: a sync cannot delete a note or a card through the port it decides with."""
    names = [n for n, _ in inspect.getmembers(NoteStoreSession) if not n.startswith("_")]
    assert names and not [n for n in names if any(w in n for w in ("delete", "remove", "forget"))]


# ---------------------------------------------------------------------------
# The fake's own policing (what the use-case's tests rely on)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fake_refuses_a_call_outside_a_session():
    with pytest.raises(RuntimeError, match="outside a session"):
        await FakeNoteStore().read_notes([1])


@pytest.mark.asyncio
async def test_the_fake_refuses_any_write_when_read_only():
    fake = FakeNoteStore()
    fake.read_only = True
    async with fake.session() as s:
        assert await s.deck_names() == {"Default"}
        with pytest.raises(ContractViolation, match="read-only"):
            await s.create_decks(["X"])


@pytest.mark.asyncio
async def test_the_fake_refuses_a_retype_that_would_drop_cards():
    fake = FakeNoteStore()
    nid = fake.add("Cloze", "C", {"Text": "{{c1::a}} {{c2::b}}"})
    async with fake.session() as s:
        with pytest.raises(ContractViolation, match="delete cards"):
            await s.apply_changes([NoteChange(nid, retype=("Basic", (0, 1)))])


@pytest.mark.asyncio
async def test_the_fake_loses_a_reply_after_the_note_landed():
    fake = FakeNoteStore()
    fake.lose = lambda note: True
    async with fake.session() as s:
        [got] = await s.create_notes(
            [NewNote("Basic", "Default", {"Front": "Q", "Back": "A"}, (ID,))]
        )
    assert isinstance(got, OutcomeUnknown)
    assert len(fake.notes) == 1


@pytest.mark.asyncio
async def test_the_fake_stops_a_note_at_its_failed_step():
    fake = FakeNoteStore()
    nid = fake.add("Basic", "C", {"Front": "Q", "Back": "A"}, ["old"])
    fake.fail = lambda op, key: Exception("boom") if op == "add_tags" else None
    async with fake.session() as s:
        [error] = await s.apply_changes(
            [NoteChange(nid, fields={"Front": "new"}, add_tags=("t",), remove_tags=("old",))]
        )
    assert str(error) == "boom"
    held = fake.held(nid)
    assert (held.fields["Front"], held.tags) == ("new", ("old",))
