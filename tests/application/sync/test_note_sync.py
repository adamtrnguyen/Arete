"""The sync use-case, `NoteSync`, against the in-memory note store.

Each test seeds what Anki holds, runs one or more batches of vault cards through
`NoteSync.sync_notes`, and checks what Anki holds after and what each card reports.
The behaviours are those of the AnkiConnect reconcile and the direct update, now decided
in one place, plus the identity rules of the shared-sync design (section 2.6).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from arete.application.sync.note_sync import NoteSync, VaultClaims
from arete.domain.models import AnkiNote, NoteTypeInfo, OutcomeUnknown, UpdateItem, WorkItem
from tests.fakes.note_store import STOCK_TYPES, FakeNoteStore

ID = "arete_01J00000000000000000000201"
ID2 = "arete_01J00000000000000000000202"
FOREIGN = "arete_01J00000000000000000000299"
SRC = "_obsidian_source"


def card(
    idx: int = 1,
    *,
    arete_id: str | None = ID,
    nid: int | str | None = None,
    cid: int | str | None = None,
    model: str = "Basic",
    deck: str = "S::Deck",
    tags: list[str] | None = None,
    **fields: str,
) -> WorkItem:
    fields = fields or {"Front": "<p>Q</p>", "Back": "<p>A</p>"}
    note = AnkiNote(
        model=model,
        deck=deck,
        fields=dict(fields),
        tags=[*(tags or []), *([arete_id] if arete_id else [])],
        start_line=1,
        end_line=1,
        source_file=Path("vault/c.md"),
        source_index=idx,
        nid=None if nid is None else str(nid),
        cid=None if cid is None else str(cid),
        arete_id=arete_id,
    )
    return WorkItem(note=note, source_file=note.source_file, source_index=idx)


async def run(
    store: FakeNoteStore, *items: WorkItem, claims: VaultClaims | None = None
) -> list[UpdateItem]:
    return await NoteSync(store, claims).sync_notes(list(items))


def writes(store: FakeNoteStore) -> int:
    return sum(
        store.calls[op] for op in ("add_field", "create_decks", "create_notes", "apply_changes")
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_card_is_created_in_its_deck_and_reports_its_ids():
    store = FakeNoteStore()

    [r] = await run(store, card(tags=["t"]))

    assert r.ok and r.error is None
    held = store.held(int(r.new_nid or 0))
    assert (held.model, held.fields["Front"], held.fields["Back"]) == (
        "Basic",
        "<p>Q</p>",
        "<p>A</p>",
    )
    assert held.tags == ("t", ID)
    assert [c.home_deck for c in held.cards] == ["S::Deck"]
    assert r.new_cid == str(held.cards[0].cid)
    assert r.note is not None


@pytest.mark.asyncio
async def test_a_new_cloze_card_reports_the_card_of_ord_0():
    store = FakeNoteStore()

    [r] = await run(store, card(model="Cloze", Text="{{c1::a}} {{c2::b}}", **{"Back Extra": ""}))

    held = store.held(int(r.new_nid or 0))
    assert [c.ord for c in held.cards] == [0, 1]
    assert r.new_cid == str(held.cards[0].cid)


@pytest.mark.asyncio
async def test_of_two_cards_with_one_first_field_in_a_deck_the_later_is_refused():
    store = FakeNoteStore()

    a, b = await run(
        store,
        card(1, Front="<p>Same</p>", Back="one"),
        card(2, arete_id=ID2, Front="Same ", Back="two"),  # one key: first_field_key
    )

    assert a.ok and not b.ok and b.new_nid is None
    assert list(store.notes) == [int(a.new_nid or 0)]
    assert b.error == (
        f"duplicate first field: c.md #2 (vault/c.md) has the same first field as note "
        f"{a.new_nid} of c.md #1 in deck 'S::Deck' (Basic). Not created: change the card's "
        "first field or its deck"
    )


@pytest.mark.asyncio
async def test_a_card_of_an_earlier_batch_holds_its_first_field_for_the_run():
    store = FakeNoteStore()
    sync = NoteSync(store)

    [a] = await sync.sync_notes([card(1, Front="Same", Back="one")])
    [b] = await sync.sync_notes([card(2, arete_id=ID2, Front="Same", Back="two")])

    assert a.ok and not b.ok and f"note {a.new_nid} of c.md #1" in (b.error or "")
    assert len(store.notes) == 1


@pytest.mark.asyncio
async def test_across_batches_run_at_once_exactly_one_is_created_then_kept():
    """Run 1 may pick either card; from run 2 on the created one is updated, the other refused."""
    store = FakeNoteStore()
    fields = {1: {"Front": "Same", "Back": "one"}, 2: {"Front": "Same", "Back": "two"}}
    ids = {1: ID, 2: ID2}
    sync = NoteSync(store)

    [a], [b] = await asyncio.gather(
        sync.sync_notes([card(1, arete_id=ID, **fields[1])]),
        sync.sync_notes([card(2, arete_id=ID2, **fields[2])]),
    )

    assert sorted([a.ok, b.ok]) == [False, True] and len(store.notes) == 1
    won, lost = (1, 2) if a.ok else (2, 1)
    nid = (a if a.ok else b).new_nid
    assert "duplicate first field" in ((b if a.ok else a).error or "")

    for _ in range(2):  # every later run, the vault now holding the winner's nid
        fields[won]["Back"] = f"{fields[won]['Back']}+"
        sync = NoteSync(store)
        [w], [r] = await asyncio.gather(
            sync.sync_notes([card(won, arete_id=ids[won], nid=nid, **fields[won])]),
            sync.sync_notes([card(lost, arete_id=ids[lost], **fields[lost])]),
        )
        assert (
            w.ok
            and w.new_nid == str(nid)
            and store.held(int(nid or 0)).fields["Back"] == (fields[won]["Back"])
        )
        assert not r.ok and f"note {nid}" in (r.error or "") and len(store.notes) == 1


@pytest.mark.asyncio
async def test_a_note_held_in_the_deck_refuses_a_new_card_and_is_left_alone():
    store = FakeNoteStore()
    other = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "theirs"}, [FOREIGN])

    [r] = await run(store, card())

    assert not r.ok and f"same first field as note {other} in deck 'S::Deck'" in (r.error or "")
    assert list(store.notes) == [other] and store.held(other).fields["Back"] == "theirs"


@pytest.mark.asyncio
async def test_the_held_notes_home_deck_counts_not_the_queue_it_is_in():
    store = FakeNoteStore()
    other = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "theirs"}, [FOREIGN])
    store.queue([other])

    [r] = await run(store, card())

    assert not r.ok and f"note {other}" in (r.error or "")


@pytest.mark.asyncio
async def test_the_same_first_field_in_another_deck_or_type_is_created():
    store = FakeNoteStore()
    store.add("Basic", "S::Other", {"Front": "<p>Q</p>", "Back": "x"}, [FOREIGN])
    store.add("Basic (and reversed card)", "S::Deck", {"Front": "<p>Q</p>", "Back": "x"}, [ID2])

    [r] = await run(store, card())

    assert r.ok and r.new_nid is not None and len(store.notes) == 3


@pytest.mark.asyncio
async def test_a_note_whose_decks_cannot_be_read_refuses_the_card():
    """Refusing is the safe side; the next sync checks again."""
    store = FakeNoteStore()
    other = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "x"}, [FOREIGN])
    store.fail = lambda op, key: RuntimeError("read broke") if op == "read_notes" else None

    [r] = await run(store, card())

    assert not r.ok and f"note {other}, whose decks could not be read" in (r.error or "")
    assert store.calls["create_notes"] == 0 and list(store.notes) == [other]


@pytest.mark.asyncio
async def test_an_identity_match_is_an_update_never_a_refusal():
    store = FakeNoteStore()
    mine = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "old"}, [ID])
    by_nid = store.add("Basic", "S::Deck", {"Front": "<p>R</p>", "Back": "old"}, [ID2])

    a, b = await run(
        store,
        card(1, Back="new"),  # found by its Arete id
        card(2, arete_id=ID2, nid=by_nid, Front="<p>R</p>", Back="new"),
    )

    assert a.ok and a.new_nid == str(mine) and b.ok and b.new_nid == str(by_nid)
    assert len(store.notes) == 2 and store.held(mine).fields["Back"] == "new"


@pytest.mark.asyncio
async def test_a_dry_run_refuses_the_duplicate_as_a_sync_would():
    store = FakeNoteStore()
    store.read_only = True
    sync = NoteSync(store, dry_run=True)

    a, b = await sync.sync_notes(
        [card(1, Front="Same", Back="one"), card(2, arete_id=ID2, Front="Same", Back="two")]
    )

    assert a.ok and not b.ok
    assert "same first field as c.md #1, which this run creates" in (b.error or "")
    assert any(
        line.startswith("would refuse c.md #2: duplicate first field") for line in sync.report
    )


@pytest.mark.asyncio
async def test_a_field_named_in_another_case_fills_the_types_field():
    store = FakeNoteStore()

    [r] = await run(
        store, card(model="Basic with Extra", front="lower", back="b", **{"Back Extra": "e"})
    )

    held = store.held(int(r.new_nid or 0))
    assert (held.fields["Front"], held.fields["Back"], held.fields["Back Extra"]) == (
        "lower",
        "b",
        "e",
    )


@pytest.mark.asyncio
async def test_the_source_field_is_added_to_a_type_once_per_run():
    store = FakeNoteStore()
    sync = NoteSync(store)

    await sync.sync_notes([card(1, **{"Front": "a", "Back": "b", SRC: "v|c.md|1|x"})])
    await sync.sync_notes([card(2, arete_id=ID2, **{"Front": "c", "Back": "d", SRC: "v|c.md|2|y"})])

    assert store.calls["add_field"] == 1
    assert store.calls["note_types"] == 1
    assert {n.fields[SRC] for n in store.notes.values()} == {"v|c.md|1|x", "v|c.md|2|y"}


@pytest.mark.asyncio
async def test_a_missing_note_type_fails_the_card():
    store = FakeNoteStore()

    [r] = await run(store, card(model="No Such Type", Front="q"))

    assert not r.ok and "not found" in (r.error or "")
    assert store.notes == {}


@pytest.mark.asyncio
async def test_a_deck_that_cannot_be_created_fails_its_card_only():
    store = FakeNoteStore()
    store.fail = lambda op, key: (
        Exception("bad name") if (op, key) == ("create_decks", "Bad") else None
    )

    bad, good = await run(store, card(1, deck="Bad"), card(2, arete_id=ID2, deck="Good"))

    assert not bad.ok and "Bad" in (bad.error or "")
    assert good.ok


# ---------------------------------------------------------------------------
# Update: compare, then write only what differs
# ---------------------------------------------------------------------------


def held_as_vault(store: FakeNoteStore, item: WorkItem, **change) -> int:
    note = item.note
    nid = store.add(
        change.get("model", note.model),
        change.get("deck", note.deck),
        {**note.fields, **change.get("fields", {})},
        change.get("tags", note.tags),
    )
    note.nid = str(nid)
    return nid


@pytest.mark.asyncio
async def test_an_unchanged_note_gets_no_write():
    store = FakeNoteStore()
    item = card()
    nid = held_as_vault(store, item)

    [r] = await run(store, item)

    assert (r.ok, r.new_nid, r.new_cid) == (True, str(nid), None)
    assert writes(store) == 0


@pytest.mark.asyncio
async def test_every_facet_that_differs_is_set_to_the_vaults_in_one_write():
    store = FakeNoteStore()
    items = [card(i, arete_id=f"arete_01J{i:023d}", tags=["new"]) for i in range(3)]
    for item in items:
        held_as_vault(store, item, fields={"Back": "old"}, deck="Elsewhere", tags=["old"])

    results = await run(store, *items)

    assert all(r.ok for r in results)
    assert store.calls["apply_changes"] == 1
    for item in items:
        held = store.held(int(item.note.nid or 0))
        assert held.fields["Back"] == "<p>A</p>"
        assert set(held.tags) == set(item.note.tags)
        assert [c.home_deck for c in held.cards] == ["S::Deck"]


@pytest.mark.asyncio
async def test_tags_compare_without_case_and_leech_stays():
    store = FakeNoteStore()
    item = card(tags=["Topic"])
    nid = held_as_vault(store, item, tags=["topic", ID.upper().replace("ARETE", "arete"), "leech"])

    [r] = await run(store, item)

    assert r.ok and writes(store) == 0
    assert "leech" in store.held(nid).tags


@pytest.mark.asyncio
async def test_a_tag_added_in_anki_is_removed_but_not_leech_or_marked():
    store = FakeNoteStore()
    item = card()
    nid = held_as_vault(store, item, tags=[ID, "anki-only", "leech", "Marked"])

    await run(store, item)

    assert sorted(store.held(nid).tags) == sorted([ID, "leech", "Marked"])


@pytest.mark.asyncio
async def test_a_split_note_is_filed_whole_under_the_vaults_deck():
    store = FakeNoteStore()
    item = card(model="Cloze", Text="{{c1::a}} {{c2::b}}", **{"Back Extra": "x"})
    nid = held_as_vault(store, item)
    store.cards[store.notes[nid].cards[1]].did = "Elsewhere"

    await run(store, item)

    assert [c.home_deck for c in store.held(nid).cards] == ["S::Deck", "S::Deck"]


@pytest.mark.asyncio
async def test_a_queued_card_keeps_its_queue_on_a_field_change():
    store = FakeNoteStore()
    item = card()
    nid = held_as_vault(store, item, fields={"Back": "old"})
    store.queue([nid])

    await run(store, item)

    held = store.held(nid)
    assert held.fields["Back"] == "<p>A</p>"
    assert [(c.home_deck, c.in_filtered) for c in held.cards] == [("S::Deck", True)]
    assert store.cards[held.cards[0].cid].did == "Arete::Queue"


@pytest.mark.asyncio
async def test_a_queued_card_gets_its_new_home_deck_and_stays_queued():
    store = FakeNoteStore()
    item = card(deck="S::Moved")
    nid = held_as_vault(store, item, deck="S::Home")
    store.queue([nid])

    await run(store, item)

    [c] = store.held(nid).cards
    assert (c.home_deck, c.in_filtered, store.cards[c.cid].did) == (
        "S::Moved",
        True,
        "Arete::Queue",
    )


@pytest.mark.asyncio
async def test_a_note_type_change_maps_fields_and_keeps_cards():
    store = FakeNoteStore()
    item = card(model="Basic with Extra", **{"Front": "Q", "Back": "A", "Back Extra": "E"})
    nid = held_as_vault(store, item, model="Basic")
    cids = store.notes[nid].cards[:]

    [r] = await run(store, item)

    held = store.held(nid)
    assert r.ok and held.model == "Basic with Extra" and held.fields["Back Extra"] == "E"
    assert [c.cid for c in held.cards] == cids


@pytest.mark.asyncio
async def test_a_type_change_that_would_drop_cards_is_refused():
    store = FakeNoteStore()
    item = card(Front="Q", Back="A")
    nid = store.add("Cloze", "S::Deck", {"Text": "{{c1::a}} {{c2::b}} {{c3::c}}"}, [ID])
    item.note.nid = str(nid)

    [r] = await run(store, item)

    assert not r.ok and "would delete 2 of its 3 cards" in (r.error or "")
    assert store.held(nid).model == "Cloze" and writes(store) == 0


@pytest.mark.asyncio
async def test_a_custom_cloze_type_takes_a_multi_card_note():
    """The cloze test is the type's kind, not its name."""
    types = {**STOCK_TYPES, "My Cloze": NoteTypeInfo("My Cloze", ("Text", "Back Extra"), True, 1)}
    store = FakeNoteStore(types)
    item = card(model="My Cloze", Text="{{c1::a}} {{c2::b}}", **{"Back Extra": ""})
    nid = store.add("Cloze", "S::Deck", {"Text": "{{c1::a}} {{c2::b}}"}, [ID])
    item.note.nid = str(nid)

    [r] = await run(store, item)

    assert r.ok and store.held(nid).model == "My Cloze"


@pytest.mark.parametrize(
    ("vault_cid", "reported"),
    [(None, None), ("valid", None), ("1", "first")],
    ids=["none", "valid", "stale"],
)
@pytest.mark.asyncio
async def test_an_update_reports_a_cid_only_to_replace_a_stale_one(vault_cid, reported):
    store = FakeNoteStore()
    item = card()
    nid = held_as_vault(store, item)
    first = str(store.notes[nid].cards[0])
    item.note.cid = first if vault_cid == "valid" else vault_cid

    [r] = await run(store, item)

    assert r.new_cid == (first if reported else None)


@pytest.mark.asyncio
async def test_a_failed_write_fails_only_its_card():
    store = FakeNoteStore()
    items = [card(i, arete_id=f"arete_01J{i:023d}") for i in range(3)]
    nids = [held_as_vault(store, item, fields={"Back": "old"}) for item in items]
    store.fail = lambda op, key: Exception("boom") if (op, key) == ("fields", nids[1]) else None

    results = await run(store, *items)

    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error == "boom" and results[1].new_nid is None


# ---------------------------------------------------------------------------
# Identity: which note a card is
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dangling_nid_is_resolved_by_the_arete_id():
    store = FakeNoteStore()
    nid = store.add("Basic", "Elsewhere", {"Front": "<p>Q</p>", "Back": "x"}, [ID])

    [r] = await run(store, card(nid=1111111111111))

    assert list(store.notes) == [nid]
    assert (r.ok, r.new_nid, r.new_cid) == (True, str(nid), str(store.notes[nid].cards[0]))
    assert [c.home_deck for c in store.held(nid).cards] == ["S::Deck"]


@pytest.mark.asyncio
async def test_a_card_whose_text_changed_is_found_by_its_id():
    store = FakeNoteStore()
    nid = store.add("Basic", "S::Deck", {"Front": "<p>Old question</p>", "Back": "x"}, [ID])

    [r] = await run(store, card(Front="<p>Reworded</p>"))

    assert list(store.notes) == [nid] and r.new_nid == str(nid)
    assert store.held(nid).fields["Front"] == "<p>Reworded</p>"


@pytest.mark.asyncio
async def test_several_notes_with_the_id_prefer_the_content_match_then_the_oldest():
    store = FakeNoteStore()
    store.add("Basic", "S", {"Front": "other"}, [ID])
    match = store.add("Basic", "S", {"Front": "<p>Q</p>"}, [ID])

    [r] = await run(store, card())

    assert r.new_nid == str(match)


@pytest.mark.asyncio
async def test_a_legacy_note_without_an_id_is_adopted_by_its_first_field():
    store = FakeNoteStore()
    nid = store.add("Basic", "Elsewhere", {"Front": "<b>q</b>", "Back": "x"}, [])

    [r] = await run(store, card())

    assert r.new_nid == str(nid)
    assert ID in store.held(nid).tags


@pytest.mark.asyncio
async def test_content_never_adopts_a_note_carrying_another_cards_id():
    store = FakeNoteStore()
    other = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "theirs"}, [FOREIGN])

    [r] = await run(store, card())

    assert r.new_nid != str(other), "never adopted"
    assert not r.ok and "duplicate first field" in (r.error or ""), "one deck: refused"
    assert store.held(other).fields["Back"] == "theirs"


@pytest.mark.asyncio
async def test_content_never_adopts_a_note_another_vault_card_declares():
    store = FakeNoteStore()
    other = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "theirs"}, [])
    claims = VaultClaims(
        nid_owners={other: frozenset({ID2.casefold()})}, arete_ids=frozenset({ID2.casefold()})
    )

    [r] = await run(store, card(), claims=claims)

    assert r.new_nid != str(other), "never adopted"
    assert not r.ok and "duplicate first field" in (r.error or ""), "one deck: refused"


@pytest.mark.asyncio
async def test_a_nid_at_another_vault_cards_note_is_a_copy_and_resolves_by_id():
    store = FakeNoteStore()
    theirs = store.add("Basic", "S::Deck", {"Front": "theirs", "Back": "x"}, [ID2])
    ours = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "x"}, [ID])
    claims = VaultClaims(
        nid_owners={theirs: frozenset({ID2.casefold()})},
        arete_ids=frozenset({ID.casefold(), ID2.casefold()}),
    )

    [r] = await run(store, card(nid=theirs), claims=claims)

    assert r.ok and r.new_nid == str(ours)
    assert store.held(theirs).fields["Front"] == "theirs"


@pytest.mark.asyncio
async def test_a_nid_at_a_note_with_an_unknown_arete_id_is_refused():
    store = FakeNoteStore()
    nid = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "theirs"}, [FOREIGN])

    [r] = await run(store, card(nid=nid), claims=VaultClaims(arete_ids=frozenset({ID.casefold()})))

    assert not r.ok and FOREIGN in (r.error or "")
    assert len(store.notes) == 1 and writes(store) == 0


@pytest.mark.parametrize(("front", "ok"), [("<p>Q</p>", True), ("<p>Something else</p>", False)])
@pytest.mark.asyncio
async def test_a_nid_at_a_note_without_an_id_is_taken_only_if_its_first_field_matches(front, ok):
    store = FakeNoteStore()
    nid = store.add("Basic", "S::Deck", {"Front": front, "Back": "x"}, ["hand"])

    [r] = await run(store, card(nid=nid))

    assert r.ok is ok
    assert len(store.notes) == 1
    assert (ID in store.held(nid).tags) is ok


@pytest.mark.asyncio
async def test_a_failed_read_fails_the_card_and_creates_nothing():
    store = FakeNoteStore()
    store.fail = lambda op, key: Exception("busy") if op == "read_notes" else None

    [r] = await run(store, card(nid=4242))

    assert not r.ok and "busy" in (r.error or "")
    assert store.notes == {}


@pytest.mark.asyncio
async def test_a_failed_id_lookup_fails_the_card_and_creates_nothing():
    store = FakeNoteStore()
    store.fail = lambda op, key: Exception("busy") if op == "find_by_arete_ids" else None

    [r] = await run(store, card())

    assert not r.ok and store.notes == {}


@pytest.mark.asyncio
async def test_a_failed_content_scan_fails_the_card_and_creates_nothing():
    store = FakeNoteStore()
    store.fail = lambda op, key: Exception("busy") if op == "scan_model" else None

    [r] = await run(store, card())

    assert not r.ok and store.notes == {}


@pytest.mark.asyncio
async def test_a_nid_that_is_not_a_number_fails_the_card():
    store = FakeNoteStore()

    [r] = await run(store, card(nid="not-a-number"))

    assert (r.ok, r.new_nid, r.new_cid) == (False, None, None)
    assert store.notes == {}


@pytest.mark.asyncio
async def test_one_note_belongs_to_one_card_per_run():
    store = FakeNoteStore()
    nid = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "x"}, [ID])

    a, b = await run(store, card(1, nid=nid), card(2, nid=nid))

    assert a.ok and not b.ok and "already synced" in (b.error or "")


@pytest.mark.asyncio
async def test_a_card_without_a_valid_id_is_not_looked_up_by_it():
    store = FakeNoteStore()
    store.add("Basic", "S::Deck", {"Front": "different", "Back": "x"}, ["arete_review"])

    [r] = await run(store, card(arete_id="arete_review"))

    assert r.ok and len(store.notes) == 2
    assert store.calls["find_by_arete_ids"] == 0


# ---------------------------------------------------------------------------
# Lost replies: never create twice in one run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_create_that_landed_is_adopted_not_sent_again():
    store = FakeNoteStore()
    store.lose = lambda note: True

    [r] = await run(store, card())

    assert r.ok and list(store.notes) == [int(r.new_nid or 0)]
    assert store.calls["create_notes"] == 1


@pytest.mark.asyncio
async def test_a_create_that_never_landed_fails_and_is_not_sent_again():
    store = FakeNoteStore()
    store.fail = lambda op, key: OutcomeUnknown("timed out") if op == "create_notes" else None

    [r] = await run(store, card())

    assert not r.ok and "next sync reconciles" in (r.error or "")
    assert store.notes == {} and store.calls["create_notes"] == 1


# ---------------------------------------------------------------------------
# The run: caches, sessions, failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_types_decks_and_the_content_index_are_read_once_per_run():
    store = FakeNoteStore()
    for i in range(3):
        store.add("Basic", "Default", {"Front": f"legacy {i}"}, [])
    sync = NoteSync(store)

    for b in range(3):
        await sync.sync_notes(
            [
                card(10 * b + i, arete_id=f"arete_01J{10 * b + i:023d}", Front=f"n{b}{i}", Back="x")
                for i in range(3)
            ]
        )

    assert (store.calls["note_types"], store.calls["deck_names"], store.calls["scan_model"]) == (
        1,
        1,
        1,
    )
    assert store.sessions == 3


@pytest.mark.asyncio
async def test_a_collection_that_cannot_be_opened_fails_every_card():
    class Closed(FakeNoteStore):
        def session(self):
            raise OSError("collection is locked")

    results = await run(Closed(), card(1), card(2, arete_id=ID2))

    assert [r.ok for r in results] == [False, False]
    assert all("collection is locked" in (r.error or "") for r in results)
    assert all(r.note is not None for r in results)


@pytest.mark.asyncio
async def test_a_failed_type_read_is_read_again_by_the_next_batch():
    store = FakeNoteStore()
    store.fail = lambda op, key: Exception("busy") if op == "note_types" else None
    sync = NoteSync(store)

    [first] = await sync.sync_notes([card(1)])
    store.fail = lambda op, key: None
    [second] = await sync.sync_notes([card(2, arete_id=ID2)])

    assert not first.ok and "busy" in (first.error or "")
    assert second.ok and store.calls["note_types"] == 2


# ---------------------------------------------------------------------------
# Dry run: the same decisions, no write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dry_run_reports_every_decision_and_writes_nothing():
    types = {**STOCK_TYPES, "Basic": NoteTypeInfo("Basic", ("Front", "Back"), False, 1)}
    store = FakeNoteStore(types)
    changed = card(
        1,
        arete_id=ID,
        tags=["t"],
        deck="S::Moved",
        **{"Front": "<p>Q</p>", "Back": "new", SRC: "s"},
    )
    held = store.add("Basic", "S::Deck", {"Front": "<p>Q</p>", "Back": "old"}, [ID, "stale"])
    changed.note.nid = str(held)
    lost = store.add("Basic", "S::Deck", {"Front": "<p>Lost</p>", "Back": "x"}, [ID2])
    adopted = card(2, arete_id=ID2, nid=1111111111111, Front="<p>Lost</p>", Back="x")
    refused = card(
        3, arete_id="arete_01J00000000000000000000203", nid=held, Front="<p>Other</p>", Back="y"
    )
    new = card(4, arete_id="arete_01J00000000000000000000204", deck="S::Fresh", Front="n", Back="m")
    store.read_only = True
    sync = NoteSync(store, dry_run=True)

    results = await sync.sync_notes([changed, adopted, refused, new])

    assert [r.ok for r in results] == [True, True, False, True]
    assert [r.new_nid for r in results] == [str(held), str(lost), None, None]
    assert all(r.new_cid is None for r in results)
    report = "\n".join(sync.report)
    assert (
        f"would overwrite nid={held} for c.md #1: fields Back, {SRC}; tags +t -stale; deck S::Deck -> S::Moved"
        in report
    )
    assert f"would adopt nid={lost} for c.md #2" in report
    assert "would refuse c.md #3" in report
    assert "would create c.md #4 in deck 'S::Fresh' (Basic)" in report
    assert "would create deck 'S::Moved'" in report and "would create deck 'S::Fresh'" in report
    assert f"would add field {SRC} to note type 'Basic'" in report
    assert (
        sum(
            store.calls[op] for op in ("add_field", "create_decks", "create_notes", "apply_changes")
        )
        == 0
    )
