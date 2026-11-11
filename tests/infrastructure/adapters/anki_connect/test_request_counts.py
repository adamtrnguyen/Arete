"""Request counts of a sync over AnkiConnect against an in-memory AnkiConnect.

The sync is `NoteSync` over the adapter's session, as the pipeline runs it. Each test pins
how many requests of each action a fixed batch costs, so a change that adds round trips
fails here. The fake answers as the add-on does (``fake_anki.py``).

The first request of a run reads the batch's note types and the deck names in one `multi`
(`getModelInfo`, `deckNames`); the counts below include it.
"""

import asyncio
from pathlib import Path

import pytest

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AddonOutdated, AnkiNote, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

from .fake_anki import FakeAnki

N = 50
TYPE_READS = {"getModelInfo": 1, "deckNames": 1}


def _id(i: int) -> str:
    return f"arete_{i:026d}"


def _item(
    i: int,
    nid: int | None = None,
    front: str | None = None,
    deck: str = "D",
    tags: list[str] | None = None,
) -> WorkItem:
    note = AnkiNote(
        model="Basic",
        deck=deck,
        fields={"Front": front or f"q{i}", "Back": "b", "_obsidian_source": f"v|f.md|{i}|x"},
        tags=[_id(i)] if tags is None else tags,
        start_line=1,
        nid=str(nid) if nid else None,
        source_file=Path("f.md"),
        source_index=i,
        arete_id=_id(i) if tags is None or _id(i) in tags else None,
    )
    return WorkItem(note=note, source_file=Path("f.md"), source_index=i)


def _adapter(fake: FakeAnki) -> AnkiConnectAdapter:
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
    adapter._http_request = fake.http  # type: ignore[method-assign]
    return adapter


def _sync_of(fake: FakeAnki) -> NoteSync:
    return NoteSync(_adapter(fake))


# ---------------------------------------------------------------------------
# Creates and heals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_notes_cost_a_fixed_number_of_requests():
    """N new cards: one id search, one content scan, one add per 50, one read of the cids."""
    fake = FakeAnki()
    results = await _sync_of(fake).sync_notes([_item(i) for i in range(N)])

    assert all(r.ok for r in results)
    assert fake.requests == {
        "multi": 3,  # types and decks; the id searches; the adds
        "findNotes": 1,  # the content scan of Basic
        "createDeck": 1,
        "notesInfo": 1,
        "getHomeDecks": 1,
    }
    assert fake.sub_actions["addNote"] == N
    for r in results:
        assert r.new_cid == str(fake.notes[int(r.new_nid or 0)]["cards"][0])


@pytest.mark.asyncio
async def test_a_note_healed_by_its_tag_is_read_once():
    fake = FakeAnki()
    nid = fake.add({"Front": "q1"}, deck="Old", tags=[_id(1)])

    [result] = await _sync_of(fake).sync_notes([_item(1)])

    assert result.ok and result.new_nid == str(nid)
    assert result.new_cid == str(fake.notes[nid]["cards"][0])
    assert fake.card_deck[fake.notes[nid]["cards"][0]] == "D"
    assert fake.requests["notesInfo"] == 1  # the id search's read serves the comparison too
    assert fake.requests["addNote"] == 0
    assert sum(fake.requests.values()) == 7


# ---------------------------------------------------------------------------
# Updates: one notesInfo and one getHomeDecks per batch, writes only where Anki differs
# ---------------------------------------------------------------------------


def _held(fake: FakeAnki, item: WorkItem, **change) -> int:
    """Put the item's note into the fake as the vault has it, then apply ``change``."""
    note = item.note
    fields = dict(note.fields, **change.get("fields", {}))
    nid = fake.add(fields, deck=change.get("deck", note.deck), tags=change.get("tags", note.tags))
    note.nid = str(nid)
    return nid


def _assert_matches_vault(fake: FakeAnki, item: WorkItem) -> None:
    note = item.note
    held = fake.notes[int(note.nid or 0)]
    assert {k: v for k, v in note.fields.items() if k in held["fields"]} == {
        k: held["fields"][k] for k in note.fields if k in held["fields"]
    }
    assert set(held["tags"]) == set(note.tags)
    assert {fake.card_deck[c] for c in held["cards"]} == {note.deck}


async def _sync(fake: FakeAnki, items: list[WorkItem]):
    fake.decks.add("D")
    fake.reset_counts()
    return await _sync_of(fake).sync_notes(items)


@pytest.mark.asyncio
async def test_unchanged_updates_send_no_write():
    fake = FakeAnki()
    items = [_item(i) for i in range(N)]
    for item in items:
        _held(fake, item)

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests == {"multi": 1, "notesInfo": 1, "getHomeDecks": 1}
    assert fake.sub_actions == TYPE_READS


@pytest.mark.asyncio
async def test_one_changed_field_per_note_is_one_multi():
    fake = FakeAnki()
    items = [_item(i) for i in range(N)]
    for item in items:
        _held(fake, item, fields={"Back": "old"})

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests == {"multi": 2, "notesInfo": 1, "getHomeDecks": 1}
    assert fake.sub_actions == {**TYPE_READS, "updateNoteFields": N}
    for item in items:
        _assert_matches_vault(fake, item)


@pytest.mark.asyncio
async def test_moved_and_retagged_notes_keep_the_write_order():
    fake = FakeAnki()
    items = [_item(i, tags=[_id(i), "new"]) for i in range(N)]
    for i, item in enumerate(items):
        _held(fake, item, fields={"Front": "old"}, deck="Elsewhere", tags=[_id(i), "old"])

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests["multi"] == 5  # the type read, then one per write step
    assert fake.sub_actions == {
        **TYPE_READS,
        "updateNoteFields": N,
        "addTags": N,
        "removeTags": N,
        "setHomeDeck": N,
    }
    for item in items:
        _assert_matches_vault(fake, item)
    order = [a for a, _ in fake.log if a not in ("multi", *TYPE_READS)]
    assert order == ["notesInfo", "getHomeDecks"] + [
        a for a in ("updateNoteFields", "addTags", "removeTags", "setHomeDeck") for _ in range(N)
    ]


@pytest.mark.asyncio
async def test_a_single_note_sends_its_writes_as_plain_actions():
    fake = FakeAnki()
    item = _item(0)
    _held(fake, item, fields={"Front": "old"}, deck="Elsewhere")

    [result] = await _sync(fake, [item])

    assert result.ok
    sent = [a for a, _ in fake.log if a not in ("multi", *TYPE_READS)]
    assert sent == ["notesInfo", "getHomeDecks", "updateNoteFields", "setHomeDeck"]


@pytest.mark.asyncio
async def test_a_failed_write_stops_that_note_only():
    """Note 1's field write fails: its later writes are not sent, the others complete."""
    fake = FakeAnki()
    items = [_item(i, tags=[_id(i), "new"]) for i in range(3)]
    nids = [_held(fake, item, fields={"Front": "old"}, deck="Elsewhere") for item in items]
    fake.fail = lambda action, params: (
        "note was not found"
        if action == "updateNoteFields" and params["note"]["id"] == nids[1]
        else None
    )

    results = await _sync(fake, items)

    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error == "note was not found"
    assert results[1].new_nid is None
    sent_for_1 = [
        a for a, p in fake.log if nids[1] in p.get("notes", []) or p.get("cards") == [nids[1] * 10]
    ]
    assert sent_for_1 == ["notesInfo"]  # no addTags, no changeDeck after the failure
    _assert_matches_vault(fake, items[0])
    _assert_matches_vault(fake, items[2])


@pytest.mark.asyncio
async def test_a_failed_multi_fails_the_notes_in_it():
    fake = FakeAnki()
    items = [_item(i) for i in range(3)]
    for item in items:
        _held(fake, item, fields={"Front": "old"})
    fake.fail = lambda action, params: (
        "Anki is busy"
        if action == "multi" and any(a["action"] == "updateNoteFields" for a in params["actions"])
        else None
    )

    results = await _sync(fake, items)

    assert [r.ok for r in results] == [False, False, False]
    assert {r.error for r in results} == {"Anki is busy"}


@pytest.mark.asyncio
async def test_moves_are_sent_when_getdecks_fails():
    fake = FakeAnki()
    items = [_item(i) for i in range(3)]
    for item in items:
        _held(fake, item)
    fake.fail = lambda action, params: "boom" if action == "getHomeDecks" else None

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.sub_actions == {**TYPE_READS, "setHomeDeck": 3}


@pytest.mark.asyncio
async def test_home_decks_decide_which_cards_are_filed():
    """A card in Default and a queued card in their home decks stay; a stray one is filed.

    getHomeDecks names a queued card's home deck and Default as itself; setHomeDeck files
    the stray card, alone in its request.
    """
    fake = FakeAnki()
    in_default = _item(0, deck="Default")
    _held(fake, in_default)
    queued = _item(1)
    nid = _held(fake, queued)
    fake.queue([nid])
    stray = _item(2)
    stray_nid = _held(fake, stray, deck="Other")

    results = await _sync(fake, [in_default, queued, stray])

    assert all(r.ok for r in results)
    assert fake.requests["setHomeDeck"] == 1
    [queued_card] = fake.notes[nid]["cards"]
    assert (fake.card_deck[queued_card], fake.card_home[queued_card]) == ("Arete::Queue", "D")
    assert fake.card_deck[fake.notes[stray_nid]["cards"][0]] == "D"


# An add-on without one of Arete's actions answers "unsupported action"; the sync stops
# and says how to fix it, and sends nothing in place of the action.
OUTDATED = "Update the arete_ankiconnect add-on and restart Anki"
STOCK_STAND_INS = {"getDecks", "changeDeck", "modelFieldNames", "modelTemplates"}


def _decks_of(fake: FakeAnki) -> set[str]:
    return {fake.card_deck[c] for n in fake.notes.values() for c in n["cards"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["getModelInfo", "getHomeDecks"])
async def test_an_addon_without_an_arete_read_stops_the_prefetch(missing):
    fake = FakeAnki()
    fake.missing_actions = {missing}
    items = [_item(i) for i in range(3)]
    nids = [_held(fake, item, deck="Other") for item in items]

    with pytest.raises(AddonOutdated, match=f"'{missing}'.*{OUTDATED}"):
        await _sync_of(fake).prefetch(nids, {"Basic"})

    assert not STOCK_STAND_INS & {a for a, _ in fake.log}


@pytest.mark.asyncio
async def test_a_batch_against_an_addon_without_getmodelinfo_fails_every_card_and_writes_nothing():
    fake = FakeAnki()
    fake.missing_actions = {"getModelInfo"}
    items = [_item(i) for i in range(3)]
    for item in items:
        _held(fake, item, deck="Other")

    results = await _sync_of(fake).sync_notes(items)

    assert [r.ok for r in results] == [False] * 3
    assert all(OUTDATED in (r.error or "") for r in results)
    assert _decks_of(fake) == {"Other"}
    assert not STOCK_STAND_INS & {a for a, _ in fake.log}


@pytest.mark.asyncio
async def test_a_filing_the_addon_lacks_fails_the_note_with_the_reason():
    fake = FakeAnki()
    fake.missing_actions = {"setHomeDeck"}
    items = [_item(i) for i in range(3)]
    for item in items:
        _held(fake, item, deck="Other")

    results = await _sync_of(fake).sync_notes(items)

    assert [r.ok for r in results] == [False] * 3
    assert all("'setHomeDeck'" in (r.error or "") for r in results)
    assert all(OUTDATED in (r.error or "") for r in results)
    assert _decks_of(fake) == {"Other"}
    assert not STOCK_STAND_INS & {a for a, _ in fake.log}


@pytest.mark.asyncio
async def test_a_note_with_a_card_out_of_place_is_moved_whole():
    fake = FakeAnki()
    item = _item(0)
    nid = fake.add(dict(item.note.fields), deck="D", tags=item.note.tags, n_cards=2)
    item.note.nid = str(nid)
    fake.card_deck[fake.notes[nid]["cards"][1]] = "Other"

    [result] = await _sync(fake, [item])

    assert result.ok
    assert ("setHomeDeck", {"cards": fake.notes[nid]["cards"], "deck": "D"}) in fake.log


@pytest.mark.asyncio
async def test_two_items_on_one_note_fail_the_second():
    """One note belongs to one card per run; the second item writes nothing."""
    fake = FakeAnki()
    first, second = _item(0, front="A"), _item(1, front="B", tags=[_id(0)])
    nid = _held(fake, first)
    second.note.nid = str(nid)
    second.note.arete_id = _id(0)

    results = await _sync(fake, [first, second])

    assert [r.ok for r in results] == [True, False]
    assert "already synced" in (results[1].error or "")
    assert fake.notes[nid]["fields"]["Front"] == "A"


@pytest.mark.asyncio
async def test_a_field_the_note_type_lacks_is_no_difference():
    fake = FakeAnki()
    item = _item(0)
    _held(fake, item)
    item.note.fields["Extra"] = "not on Basic"

    [result] = await _sync(fake, [item])

    assert result.ok
    assert "updateNoteFields" not in [a for a, _ in fake.log]


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(5))
async def test_random_batches_end_with_anki_as_the_vault_says(seed):
    """Whatever Anki holds, after one sync every note it accepted matches the vault."""
    import random

    rng = random.Random(seed)
    fake = FakeAnki()
    items = [_item(i, deck=rng.choice(["D", "E", "Default"])) for i in range(N)]
    for item in items:
        change: dict = {}
        if rng.random() < 0.3:
            change["fields"] = {rng.choice(["Front", "Back", "_obsidian_source"]): "stale"}
        if rng.random() < 0.3:
            change["deck"] = rng.choice(["D", "E", "Default", "Arete::Queue"])
        if rng.random() < 0.3:
            change["tags"] = rng.choice([[], ["x"], item.note.tags + ["x"]])
        _held(fake, item, **change)
        if rng.random() < 0.1:
            item.note.fields["Back"] = item.note.fields["Back"] + "́"  # combining mark
    writes = {"updateNoteFields", "addTags", "removeTags", "setHomeDeck", "getHomeDecks"}
    fake.fail = lambda action, params: "boom" if action in writes and rng.random() < 0.05 else None

    results = await _sync(fake, items)

    assert any(r.ok for r in results)
    for item, result in zip(items, results, strict=True):
        if result.ok:
            _assert_matches_vault(fake, item)


# ---------------------------------------------------------------------------
# Content index: one collection scan per model, however many new cards ask at once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_cards_share_one_content_index_build():
    fake = FakeAnki()
    for j in range(1200):
        fake.add({"Front": f"old {j}"})
    twin = fake.add({"Front": "q7"}, deck="Elsewhere")  # no tag: found by content only

    results = await _sync(fake, [_item(i, tags=[]) for i in range(20)])

    assert all(r.ok for r in results)
    scans = [p for a, p in fake.log if a == "findNotes"]
    assert scans == [{"query": '"note:Basic"'}]
    assert fake.requests == {
        "multi": 2,  # types and decks; the 19 adds
        "findNotes": 1,
        # ceil(1201 / 500) for the index, 1 for the healed twin, 1 for the new cids
        "notesInfo": 3 + 1 + 1,
        "getHomeDecks": 2,
        "updateNoteFields": 1,
        "setHomeDeck": 1,
    }
    assert fake.sub_actions["addNote"] == 19
    assert results[7].new_nid == str(twin)


@pytest.mark.asyncio
async def test_a_failed_index_build_fails_its_cards_and_the_next_batch_scans_again():
    fake = FakeAnki()
    fake.decks.add("D")
    fake.fail = lambda action, params: "busy" if action == "findNotes" else None
    sync = _sync_of(fake)

    first = await sync.sync_notes([_item(i, tags=[]) for i in range(3)])
    fake.fail = lambda action, params: None
    fake.reset_counts()
    second = await sync.sync_notes([_item(i, tags=[]) for i in range(3, 6)])

    assert not any(r.ok for r in first)
    assert all(r.ok for r in second)
    assert len(fake.notes) == 3  # no twin from the failed batch
    assert fake.requests["findNotes"] == 1  # scanned once for the whole second batch


# ---------------------------------------------------------------------------
# Batch setup: decks in one request, nothing created twice by concurrent batches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_batches_create_each_deck_and_check_each_type_once():
    fake = FakeAnki()
    fake.models["Basic"].remove("_obsidian_source")
    sync = _sync_of(fake)

    batches = [[_item(10 * b + i, deck="Shared") for i in range(3)] for b in range(4)]
    results = await asyncio.gather(*(sync.sync_notes(batch) for batch in batches))

    assert all(r.ok for batch in results for r in batch)
    assert fake.requests["createDeck"] == 1
    assert fake.sub_actions["getModelInfo"] == 1
    assert fake.requests["modelFieldAdd"] == 1


@pytest.mark.asyncio
async def test_a_batch_creates_its_decks_in_one_request():
    fake = FakeAnki()
    items = [_item(i, deck=f"Deck{i % 5}") for i in range(N)]

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests["createDeck"] == 0
    assert fake.sub_actions["createDeck"] == 5
    assert {fake.card_deck[c] for c in fake.card_deck} == {f"Deck{i}" for i in range(5)}


@pytest.mark.asyncio
async def test_a_deck_that_failed_is_retried_and_the_others_are_kept():
    fake = FakeAnki()
    sync = _sync_of(fake)
    fake.fail = lambda action, params: (
        "bad name" if action == "createDeck" and params["deck"] == "B" else None
    )

    first = await sync.sync_notes([_item(i, deck=d) for i, d in enumerate("ABC")])
    fake.fail = lambda action, params: None
    fake.reset_counts()
    second = await sync.sync_notes([_item(i, deck=d) for i, d in enumerate("ABC", start=3)])

    assert [r.ok for r in first] == [True, False, True]
    assert all(r.ok for r in second)
    assert [p for a, p in fake.log if a == "createDeck"] == [{"deck": "B"}]


# ---------------------------------------------------------------------------
# Queue: due notes mapped to Arete ids in chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_notes_are_mapped_to_arete_ids_in_chunks_of_500():
    fake = FakeAnki()
    nids = [fake.add({"Front": f"f{i}"}, tags=["x", f"arete_{i}"]) for i in range(1200)]

    ids = await _adapter(fake).map_nids_to_arete_ids(nids)

    assert ids == [f"arete_{i}" for i in range(1200)]
    assert [len(p["notes"]) for a, p in fake.log if a == "notesInfo"] == [500, 500, 200]


@pytest.mark.asyncio
async def test_a_failed_chunk_fails_the_mapping_as_a_failed_request_does():
    fake = FakeAnki()
    nids = [fake.add({"Front": f"f{i}"}, tags=[f"arete_{i}"]) for i in range(1200)]
    fake.fail = lambda action, params: "busy" if params.get("notes", [0])[0] == nids[500] else None

    with pytest.raises(Exception, match="busy"):
        await _adapter(fake).map_nids_to_arete_ids(nids)


# ---------------------------------------------------------------------------
# Every sync compares every card: the cost of a vault where nothing changed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seven_thousand_unchanged_cards_cost_at_most_32_requests():
    """One prefetch reads every note; the 140 batches then read and write nothing."""
    fake = FakeAnki()
    fake.decks.add("D")
    items = [_item(i) for i in range(7000)]
    for item in items:
        _held(fake, item)
    fake.reset_counts()
    sync = _sync_of(fake)

    await sync.prefetch([int(i.note.nid or 0) for i in items], {"Basic"})
    results = []
    for start in range(0, len(items), 50):
        results += await sync.sync_notes(items[start : start + 50])

    assert all(r.ok and r.changed is False for r in results)
    assert sum(fake.requests.values()) <= 32
    assert fake.requests == {"multi": 1, "notesInfo": 14, "getHomeDecks": 14}
