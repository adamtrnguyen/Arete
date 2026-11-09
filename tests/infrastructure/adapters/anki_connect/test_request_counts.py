"""Request counts of AnkiConnectAdapter against an in-memory AnkiConnect.

Each test pins how many requests of each action a fixed batch costs, so a change that
adds round trips fails here. The fake answers as the add-on does (``fake_anki.py``).
"""

from pathlib import Path

import pytest

from arete.domain.models import AnkiNote, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

from .fake_anki import FakeAnki

N = 50


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
        tags=[f"arete_{i:026d}"] if tags is None else tags,
        start_line=1,
        end_line=2,
        nid=str(nid) if nid else None,
        source_file=Path("f.md"),
        source_index=i,
    )
    return WorkItem(note=note, source_file=Path("f.md"), source_index=i)


def _adapter(fake: FakeAnki) -> AnkiConnectAdapter:
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
    adapter._http_request = fake.http  # type: ignore[method-assign]
    return adapter


@pytest.mark.asyncio
async def test_new_notes_read_their_cids_in_one_request():
    fake = FakeAnki()
    results = await _adapter(fake).sync_notes([_item(i) for i in range(N)])

    assert all(r.ok for r in results)
    assert fake.requests["addNote"] == N
    assert fake.requests["notesInfo"] == 1
    for r in results:
        assert r.new_cid == str(fake.notes[int(r.new_nid or 0)]["cards"][0])


@pytest.mark.asyncio
async def test_a_note_healed_by_its_tag_is_read_once_plus_the_batch_cid_read():
    fake = FakeAnki()
    nid = fake.add({"Front": "q1"}, deck="Old", tags=[f"arete_{1:026d}"])

    [result] = await _adapter(fake).sync_notes([_item(1)])

    assert result.ok and result.new_nid == str(nid)
    assert result.new_cid == str(fake.notes[nid]["cards"][0])
    assert fake.card_deck[fake.notes[nid]["cards"][0]] == "D"
    # tag candidates (1) + the batch cid read (1); no notesInfo of its own for the heal
    assert fake.requests["notesInfo"] == 2
    assert fake.requests["addNote"] == 0


# ---------------------------------------------------------------------------
# Updates: one notesInfo and one getDecks per batch, writes only where Anki differs
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
    fake.reset_counts()
    return await _adapter(fake).sync_notes(items)


@pytest.mark.asyncio
async def test_unchanged_updates_send_no_write():
    fake = FakeAnki()
    items = [_item(i) for i in range(N)]
    for item in items:
        _held(fake, item)

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests == {"createDeck": 1, "modelFieldNames": 1, "notesInfo": 1, "getDecks": 1}


@pytest.mark.asyncio
async def test_one_changed_field_per_note_is_one_multi():
    fake = FakeAnki()
    items = [_item(i) for i in range(N)]
    for item in items:
        _held(fake, item, fields={"Back": "old"})

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests == {
        "createDeck": 1,
        "modelFieldNames": 1,
        "notesInfo": 1,
        "getDecks": 1,
        "multi": 1,
    }
    assert fake.sub_actions == {"updateNoteFields": N}
    for item in items:
        _assert_matches_vault(fake, item)


@pytest.mark.asyncio
async def test_moved_and_retagged_notes_keep_the_write_order():
    fake = FakeAnki()
    items = [_item(i, tags=[f"arete_{i:026d}", "new"]) for i in range(N)]
    for item in items:
        _held(fake, item, fields={"Front": "old"}, deck="Elsewhere", tags=["old"])

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.requests["multi"] == 4
    assert fake.sub_actions == {
        "updateNoteFields": N,
        "addTags": N,
        "removeTags": N,
        "changeDeck": N,
    }
    for item in items:
        _assert_matches_vault(fake, item)
    order = [a for a, _ in fake.log if a not in ("multi", "createDeck", "modelFieldNames")]
    assert order == ["notesInfo", "getDecks"] + [
        a for a in ("updateNoteFields", "addTags", "removeTags", "changeDeck") for _ in range(N)
    ]


@pytest.mark.asyncio
async def test_a_single_note_sends_its_writes_as_plain_actions():
    fake = FakeAnki()
    item = _item(0)
    _held(fake, item, fields={"Front": "old"}, deck="Elsewhere")

    [result] = await _sync(fake, [item])

    assert result.ok
    sent = [a for a, _ in fake.log if a not in ("createDeck", "modelFieldNames")]
    assert sent == ["notesInfo", "getDecks", "updateNoteFields", "changeDeck"]


@pytest.mark.asyncio
async def test_a_failed_write_stops_that_note_only():
    """Note 1's field write fails: its later writes are not sent, the others complete."""
    fake = FakeAnki()
    items = [_item(i, tags=[f"arete_{i:026d}", "new"]) for i in range(3)]
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
    fake.fail = lambda action, params: "Anki is busy" if action == "multi" else None

    results = await _sync(fake, items)

    assert [r.ok for r in results] == [False, False, False]
    assert {r.error for r in results} == {"Anki is busy"}


@pytest.mark.asyncio
async def test_moves_are_sent_when_getdecks_fails():
    fake = FakeAnki()
    items = [_item(i) for i in range(3)]
    for item in items:
        _held(fake, item)
    fake.fail = lambda action, params: "boom" if action == "getDecks" else None

    results = await _sync(fake, items)

    assert all(r.ok for r in results)
    assert fake.sub_actions == {"changeDeck": 3}


@pytest.mark.asyncio
async def test_a_card_reported_in_default_or_another_deck_is_moved():
    """Neither a card reported in Default nor one in a filtered deck proves a move unneeded.

    The add-on's getDecks says Default for a card whose deck is gone, and a filtered card
    names its filtered deck.
    """
    fake = FakeAnki()
    in_default = _item(0, deck="Default")
    _held(fake, in_default)
    queued = _item(1)
    nid = _held(fake, queued)
    fake.card_deck[fake.notes[nid]["cards"][0]] = "Arete::Queue"

    results = await _sync(fake, [in_default, queued])

    assert all(r.ok for r in results)
    assert fake.sub_actions == {"changeDeck": 2}
    assert fake.card_deck[fake.notes[nid]["cards"][0]] == "D"


@pytest.mark.asyncio
async def test_a_note_with_a_card_out_of_place_is_moved_whole():
    fake = FakeAnki()
    item = _item(0)
    nid = fake.add(dict(item.note.fields), deck="D", tags=item.note.tags, n_cards=2)
    item.note.nid = str(nid)
    fake.card_deck[fake.notes[nid]["cards"][1]] = "Other"

    [result] = await _sync(fake, [item])

    assert result.ok
    assert ("changeDeck", {"cards": fake.notes[nid]["cards"], "deck": "D"}) in fake.log


@pytest.mark.asyncio
async def test_two_items_on_one_note_both_write_in_item_order():
    fake = FakeAnki()
    first, second = _item(0, front="A"), _item(1, front="B")
    nid = _held(fake, first)
    second.note.nid = str(nid)

    results = await _sync(fake, [first, second])

    assert all(r.ok for r in results)
    sent = [a for a, _ in fake.log]
    assert sent.count("updateNoteFields") == 2
    assert sent.count("changeDeck") == 2
    assert fake.notes[nid]["fields"]["Front"] == "B"


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
    """Whatever Anki holds, after one sync every note matches the vault."""
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
    fake.fail = lambda action, params: "boom" if rng.random() < 0.05 else None

    results = await _sync(fake, items)

    for item, result in zip(items, results, strict=True):
        if result.ok:
            _assert_matches_vault(fake, item)
