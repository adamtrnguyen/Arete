"""AnkiConnectAdapter when Anki refuses, answers wrongly, or holds a look-alike note.

Each case asserts what the card's result says and what reached the collection. A failed
card must leave the note it targets untouched and must not create a second one.
"""

import logging
from pathlib import Path

import pytest

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AnkiNote, OutcomeUnknown, WorkItem
from arete.domain.ownership import is_arete_id
from arete.infrastructure.adapters import anki_connect
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

from .fake_anki import FakeAnki

ID = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N8"
OTHER_ID = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N9"


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(anki_connect, "_RETRY_DELAY", 0.0, raising=False)
    monkeypatch.setattr(anki_connect, "_NOT_SENT_DELAY", 0.0, raising=False)


def _item(
    *,
    front: str = "q",
    back: str = "a",
    tags: list[str] | None = None,
    nid: str | int | None = None,
    model: str = "Basic",
    index: int = 1,
) -> WorkItem:
    note = AnkiNote(
        model=model,
        deck="D",
        fields={"Front": front, "Back": back, "_obsidian_source": "v|f.md|1|x"},
        tags=[ID] if tags is None else tags,
        start_line=1,
        nid=str(nid) if nid else None,
        source_file=Path("f.md"),
        source_index=index,
        arete_id=next((t for t in (tags if tags is not None else [ID]) if is_arete_id(t)), None),
    )
    return WorkItem(note=note, source_file=Path("f.md"), source_index=index)


def _adapter(fake: FakeAnki) -> AnkiConnectAdapter:
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
    adapter._http_request = fake.http  # type: ignore[method-assign]
    return adapter


def _synced(fake: FakeAnki) -> NoteSync:
    """The adapter as the pipeline drives it: through the shared NoteSync."""
    return NoteSync(_adapter(fake))


def _held(fake: FakeAnki, front: str = "q", back: str = "OLD") -> int:
    fake.decks.add("D")
    return fake.add(
        {"Front": front, "Back": back, "_obsidian_source": "v|f.md|1|x"}, deck="D", tags=[ID]
    )


# --- reading the target note fails ---


@pytest.mark.asyncio
async def test_a_nid_that_is_not_a_number_fails_the_card_and_creates_nothing():
    fake = FakeAnki()
    [result] = await _synced(fake).sync_notes([_item(nid="not-a-number")])
    assert not result.ok
    assert result.new_nid is None
    assert fake.notes == {}
    assert fake.requests["addNote"] == 0


@pytest.mark.asyncio
async def test_when_notesinfo_errors_the_card_fails_and_no_twin_is_added():
    fake = FakeAnki()
    nid = _held(fake)
    fake.fail = lambda action, _p: "collection is locked" if action == "notesInfo" else None

    [result] = await _synced(fake).sync_notes([_item(nid=nid, back="NEW")])

    assert not result.ok
    assert "collection is locked" in (result.error or "")
    assert fake.notes[nid]["fields"]["Back"] == "OLD"
    assert fake.requests["addNote"] == 0
    assert fake.requests["updateNoteFields"] == 0


@pytest.mark.asyncio
async def test_when_notesinfo_answers_the_wrong_count_the_card_fails_untouched():
    fake = FakeAnki()
    nid = _held(fake)
    fake._a_notesInfo = lambda notes: []  # type: ignore[method-assign]

    [result] = await _synced(fake).sync_notes([_item(nid=nid, back="NEW")])

    assert not result.ok
    assert "notesInfo answered" in (result.error or "")
    assert fake.notes[nid]["fields"]["Back"] == "OLD"
    assert fake.requests["addNote"] == 0


# --- the writes fail ---


@pytest.mark.asyncio
async def test_one_failing_write_fails_only_its_own_card():
    fake = FakeAnki()
    first, second = _held(fake, front="q1"), _held(fake, front="q2")
    fake.fail = lambda action, p: (
        "note is read-only" if action == "updateNoteFields" and p["note"]["id"] == first else None
    )

    bad, good = await _synced(fake).sync_notes(
        [
            _item(front="q1", nid=first, back="NEW", index=1),
            _item(front="q2", nid=second, back="NEW", index=2),
        ]
    )

    assert not bad.ok
    assert "note is read-only" in (bad.error or "")
    assert good.ok
    assert fake.notes[first]["fields"]["Back"] == "OLD"
    assert fake.notes[second]["fields"]["Back"] == "NEW"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_reply,text",
    [
        (lambda actions: [], "multi answered"),
        (lambda actions: [{"result": None, "error": None}], "multi answered"),
        (lambda actions: [7] * len(actions), "malformed"),
    ],
    ids=["no-replies", "short", "sub-reply-not-a-dict"],
)
async def test_a_malformed_multi_reply_fails_the_cards_whose_writes_it_carried(bad_reply, text):
    """Two notes' first writes travel in one multi; a lone write is sent as itself."""
    fake = FakeAnki()
    first, second = _held(fake, front="q1"), _held(fake, front="q2")
    real_multi = fake._a_multi

    def multi(actions):
        if any(a["action"] == "updateNoteFields" for a in actions):
            return bad_reply(actions)
        return real_multi(actions)

    fake._a_multi = multi  # type: ignore[method-assign]

    results = await _synced(fake).sync_notes(
        [
            _item(front="q1", nid=first, back="NEW", index=1),
            _item(front="q2", nid=second, back="NEW", index=2),
        ]
    )

    assert [r.ok for r in results] == [False, False]
    assert all(text in (r.error or "") for r in results)


@pytest.mark.asyncio
async def test_an_add_that_answers_null_fails_the_card():
    fake = FakeAnki()
    fake.decks.add("D")
    fake._a_addNote = lambda note: None  # type: ignore[method-assign]

    [result] = await _synced(fake).sync_notes([_item()])

    assert not result.ok
    assert "addNote returned null ID" in (result.error or "")
    assert result.new_nid is None


@pytest.mark.asyncio
async def test_a_retype_whose_reply_was_lost_and_whose_reread_fails_is_reported():
    fake = FakeAnki(
        models={
            "Basic": ["Front", "Back", "_obsidian_source"],
            "Swapped": ["Back", "Front", "_obsidian_source"],
        }
    )
    nid = _held(fake)
    reads = 0

    def fail(action, _params):
        nonlocal reads
        if action == "notesInfo":
            reads += 1
            return "collection is locked" if reads > 1 else None
        return None

    fake.fail = fail
    fake.interrupt = lambda action, _p: (
        (True, TimeoutError()) if action == "changeNoteType" else None
    )

    [result] = await _synced(fake).sync_notes([_item(nid=nid, model="Swapped")])

    assert not result.ok
    assert f"Re-reading note {nid} failed: collection is locked" in (result.error or "")
    assert fake.requests["changeNoteType"] == 1


# --- look-alike notes ---


@pytest.mark.asyncio
async def test_a_failed_tag_search_fails_the_card_without_a_twin():
    """A failed lookup is not "no such note": creating then would duplicate the card."""
    fake = FakeAnki()
    nid = _held(fake)
    fake.fail = lambda action, p: (
        "search is broken"
        if action == "findNotes" and p.get("query", "").startswith('"tag:')
        else None
    )

    [result] = await _synced(fake).sync_notes([_item(back="NEW")])

    assert not result.ok
    assert "search is broken" in (result.error or "")
    assert len(fake.notes) == 1
    assert fake.notes[nid]["fields"]["Back"] == "OLD"
    assert fake.requests["addNote"] == 0


@pytest.mark.asyncio
async def test_of_two_notes_carrying_the_id_the_one_whose_content_matches_is_taken(caplog):
    fake = FakeAnki()
    fake.decks.add("D")
    stranger = fake.add({"Front": "something else"}, deck="D", tags=[ID])
    own = fake.add({"Front": "q"}, deck="D", tags=[ID])

    with caplog.at_level(logging.WARNING):
        [result] = await _synced(fake).sync_notes([_item(front="q")])

    assert result.ok, result.error
    assert result.new_nid == str(own)
    assert fake.notes[stranger]["fields"]["Front"] == "something else"
    assert len(fake.notes) == 2
    assert f"using nid={own}" in caplog.text


@pytest.mark.asyncio
async def test_duplicates_sharing_an_id_heal_to_the_oldest_note_and_warn(caplog):
    fake = FakeAnki()
    fake.decks.add("D")
    older = fake.add({"Front": "q"}, deck="D", tags=[ID])
    newer = fake.add({"Front": "q"}, deck="D", tags=[ID])

    with caplog.at_level(logging.WARNING):
        [result] = await _synced(fake).sync_notes([_item(front="q", back="NEW")])

    assert older < newer
    assert result.new_nid == str(older)
    assert fake.notes[older]["fields"]["Back"] == "NEW"
    assert fake.notes[newer]["fields"]["Back"] == ""
    assert f"using nid={older}" in caplog.text


@pytest.mark.asyncio
async def test_a_card_with_an_empty_first_field_is_never_matched_to_a_note():
    """Not by content: an empty first field matches nothing (the note carries no id)."""
    fake = FakeAnki()
    fake.decks.add("D")
    fake.add({"Front": ""}, deck="D", tags=[])

    [result] = await _synced(fake).sync_notes([_item(front="")])

    assert not result.ok
    assert "empty" in (result.error or "")
    assert len(fake.notes) == 1


@pytest.mark.asyncio
async def test_the_arete_id_field_on_the_note_wins_over_tags():
    fake = FakeAnki()
    fake.decks.add("D")
    own = fake.add({"Front": "q"}, deck="D", tags=[OTHER_ID])
    item = _item(front="q", tags=[ID])
    item.note.arete_id = OTHER_ID

    [result] = await _synced(fake).sync_notes([item])

    assert result.new_nid == str(own)
    assert len(fake.notes) == 1


# --- malformed replies at the transport level ---


@pytest.mark.asyncio
async def test_a_malformed_reply_to_a_write_may_have_landed_so_it_is_not_resent():
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
    sent = []

    async def http(payload):
        sent.append(payload["action"])
        return {"result": 1}

    adapter._http_request = http  # type: ignore[method-assign]
    with pytest.raises(OutcomeUnknown):
        await adapter._invoke("addNote", note={})
    assert sent == ["addNote"]


@pytest.mark.asyncio
async def test_a_malformed_reply_to_a_read_is_an_error_not_a_guess():
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")

    async def http(payload):
        return {"result": [], "error": None, "extra": 1}

    adapter._http_request = http  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="unexpected number of fields"):
        await adapter._invoke("deckNames")


# --- reads used by queue and prune ---


@pytest.mark.asyncio
async def test_mapping_no_nids_asks_anki_nothing():
    fake = FakeAnki()
    assert await _adapter(fake).map_nids_to_arete_ids([]) == []
    assert not fake.requests


@pytest.mark.asyncio
async def test_a_failed_nid_mapping_raises_rather_than_returning_a_partial_list():
    fake = FakeAnki()
    fake.decks.add("D")
    nids = [fake.add({"Front": f"q{i}"}, deck="D", tags=[f"arete_{i:026d}"]) for i in range(3)]
    fake.fail = lambda action, _p: "locked" if action == "notesInfo" else None
    with pytest.raises(Exception, match="locked"):
        await _adapter(fake).map_nids_to_arete_ids(nids)
