"""What the adapter does when Anki's reply to a request is lost.

A timeout or a dropped connection leaves it unknown whether Anki applied the request.
Reads, and writes that set a state, are sent again. ``addNote`` and ``changeNoteType`` are
not: sent twice, the first makes a second note and the second re-maps converted fields.
The adapter asks Anki what it holds instead. The fake applies the request first and then
loses the reply, as Anki does when it answers after the timeout.
"""

import ast
import inspect
from pathlib import Path

import httpx
import pytest

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AnkiNote, WorkItem
from arete.domain.ownership import is_arete_id
from arete.infrastructure.adapters import anki_connect
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

from .fake_anki import FakeAnki

ID = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N8"
DROPPED = httpx.RemoteProtocolError("Server disconnected without sending a response.")
REFUSED = httpx.ConnectError("[Errno 61] Connection refused")


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(anki_connect, "_RETRY_DELAY", 0.0, raising=False)
    monkeypatch.setattr(anki_connect, "_NOT_SENT_DELAY", 0.0, raising=False)


def _item(tags: list[str] | None = None, nid: int | None = None, model: str = "Basic") -> WorkItem:
    note = AnkiNote(
        model=model,
        deck="D",
        fields={"Front": "q", "Back": "a", "_obsidian_source": "v|f.md|1|x"},
        tags=[ID] if tags is None else tags,
        start_line=1,
        nid=str(nid) if nid else None,
        source_file=Path("f.md"),
        source_index=1,
        arete_id=next((t for t in (tags if tags is not None else [ID]) if is_arete_id(t)), None),
    )
    return WorkItem(note=note, source_file=Path("f.md"), source_index=1)


def _adapter(fake: FakeAnki) -> AnkiConnectAdapter:
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
    adapter._http_request = fake.http  # type: ignore[method-assign]
    return adapter


def _synced(fake: FakeAnki) -> NoteSync:
    """The adapter as the pipeline drives it: through the shared NoteSync."""
    return NoteSync(_adapter(fake))


def _first(action: str, applied: bool, exc: BaseException):
    """Lose the reply to the first request of ``action``; later ones are answered."""
    fired = False

    def interrupt(sent: str, _params: dict):
        nonlocal fired
        if sent == action and not fired:
            fired = True
            return applied, exc
        return None

    return interrupt


# ---------------------------------------------------------------------------
# addNote: never sent twice for one card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [TimeoutError(), DROPPED], ids=["timeout", "dropped"])
async def test_an_add_that_landed_is_adopted_not_sent_again(lost):
    fake = FakeAnki()
    fake.interrupt = _first("addNote", applied=True, exc=lost)

    [result] = await _synced(fake).sync_notes([_item()])

    assert fake.requests["addNote"] == 1
    assert len(fake.notes) == 1
    [nid] = fake.notes
    assert result.ok, result.error
    assert result.new_nid == str(nid)
    assert result.new_cid == str(fake.notes[nid]["cards"][0])


@pytest.mark.asyncio
async def test_an_add_that_never_landed_fails_the_card_and_is_not_sent_again():
    fake = FakeAnki()
    fake.interrupt = _first("addNote", applied=False, exc=TimeoutError())

    [result] = await _synced(fake).sync_notes([_item()])

    assert fake.requests["addNote"] == 1
    assert fake.notes == {}
    assert not result.ok
    assert result.new_nid is None
    assert "may or may not" in (result.error or "")
    assert "next sync" in (result.error or "")


@pytest.mark.asyncio
async def test_a_refused_connection_on_add_is_retried():
    """A refused connection proves nothing was sent, so sending again is safe."""
    fake = FakeAnki()
    fake.interrupt = _first("addNote", applied=False, exc=REFUSED)

    [result] = await _synced(fake).sync_notes([_item()])

    assert result.ok, result.error
    assert fake.requests["addNote"] == 2
    assert len(fake.notes) == 1


@pytest.mark.asyncio
async def test_a_card_heals_to_the_note_with_its_own_id_not_one_sharing_a_file_tag():
    """A file tag such as ``arete_review`` comes before the card's id in its tags.

    Another card of the same file has the same front and the older note.
    """
    fake = FakeAnki()
    other = fake.add({"Front": "q"}, deck="D", tags=["arete_review", f"arete_{0:026d}"])
    own = fake.add({"Front": "q"}, deck="Old", tags=["arete_review", ID])

    [result] = await _synced(fake).sync_notes([_item(tags=["arete_review", ID])])

    assert result.ok, result.error
    assert result.new_nid == str(own)
    assert fake.notes[other]["fields"]["Back"] == ""  # the other card's note is untouched
    assert fake.requests["addNote"] == 0


@pytest.mark.asyncio
async def test_an_add_that_landed_is_adopted_when_a_file_tag_precedes_the_id():
    fake = FakeAnki()
    fake.decks.add("D")
    fake.add({"Front": "another card"}, deck="D", tags=["arete_review"])
    fake.interrupt = _first("addNote", applied=True, exc=TimeoutError())

    [result] = await _synced(fake).sync_notes([_item(tags=["arete_review", ID])])

    assert fake.requests["addNote"] == 1
    [created] = [nid for nid, n in fake.notes.items() if ID in n["tags"]]
    assert result.ok, result.error
    assert result.new_nid == str(created)


@pytest.mark.asyncio
async def test_an_add_inside_a_multi_is_not_sent_again():
    from arete.domain.models import OutcomeUnknown

    fake = FakeAnki()
    fake.decks.add("D")
    fake.interrupt = _first("multi", applied=True, exc=TimeoutError())
    add = {"deckName": "D", "modelName": "Basic", "fields": {"Front": "q"}, "tags": [ID]}

    with pytest.raises(OutcomeUnknown):
        await _adapter(fake)._invoke(
            "multi", actions=[{"action": "addNote", "version": 6, "params": {"note": add}}]
        )
    assert fake.requests["multi"] == 1
    assert len(fake.notes) == 1


@pytest.mark.asyncio
async def test_an_action_missing_from_the_table_is_not_sent_again():
    from arete.domain.models import OutcomeUnknown

    fake = FakeAnki()
    fake.interrupt = _first("someFutureAction", applied=False, exc=TimeoutError())

    with pytest.raises(OutcomeUnknown):
        await _adapter(fake)._invoke("someFutureAction")
    assert fake.requests["someFutureAction"] == 1


# ---------------------------------------------------------------------------
# changeNoteType: re-read, never re-sent
# ---------------------------------------------------------------------------


def _retype_fake() -> tuple[FakeAnki, int]:
    """A Basic note the vault now declares as ``Swapped``, whose fields run Back, Front."""
    fake = FakeAnki(
        models={
            "Basic": ["Front", "Back", "_obsidian_source"],
            "Swapped": ["Back", "Front", "_obsidian_source"],
        }
    )
    nid = fake.add(
        {"Front": "q", "Back": "a", "_obsidian_source": "v|f.md|1|x"}, deck="D", tags=[ID]
    )
    return fake, nid


@pytest.mark.asyncio
async def test_a_retype_that_landed_counts_as_done():
    fake, nid = _retype_fake()
    fake.interrupt = _first("changeNoteType", applied=True, exc=TimeoutError())

    [result] = await _synced(fake).sync_notes([_item(nid=nid, model="Swapped")])

    assert fake.requests["changeNoteType"] == 1
    assert result.ok, result.error
    assert result.new_nid == str(nid)
    held = fake.notes[nid]
    assert held["model"] == "Swapped"
    assert held["fields"] == {"Back": "a", "Front": "q", "_obsidian_source": "v|f.md|1|x"}


@pytest.mark.asyncio
async def test_a_retype_that_never_landed_fails_the_card_and_is_not_sent_again():
    fake, nid = _retype_fake()
    fake.interrupt = _first("changeNoteType", applied=False, exc=TimeoutError())

    [result] = await _synced(fake).sync_notes([_item(nid=nid, model="Swapped")])

    assert fake.requests["changeNoteType"] == 1
    assert not result.ok
    assert fake.notes[nid]["model"] == "Basic"
    assert "may or may not" in (result.error or "")
    sent = [a for a, _ in fake.log]
    assert "updateNoteFields" not in sent and "setHomeDeck" not in sent


# ---------------------------------------------------------------------------
# Reads and writes that set a state: sent again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [TimeoutError(), REFUSED], ids=["timeout", "refused"])
async def test_a_read_whose_reply_is_lost_is_sent_again(lost):
    fake = FakeAnki()
    fake.interrupt = _first("deckNames", applied=True, exc=lost)

    assert await _adapter(fake).get_deck_names() == ["Default"]
    assert fake.requests["deckNames"] == 2


@pytest.mark.asyncio
async def test_a_field_write_whose_reply_is_lost_is_sent_again():
    fake = FakeAnki()
    item = _item()
    nid = fake.add({"Front": "q", "Back": "old"}, deck="D", tags=[ID])
    item.note.nid = str(nid)
    fake.interrupt = _first("updateNoteFields", applied=True, exc=TimeoutError())

    [result] = await _synced(fake).sync_notes([item])

    assert result.ok, result.error
    assert fake.requests["updateNoteFields"] == 2
    assert fake.notes[nid]["fields"]["Back"] == "a"


@pytest.mark.asyncio
async def test_a_multi_of_state_writes_whose_reply_is_lost_is_sent_again():
    fake = FakeAnki()
    items = []
    for i in range(3):
        item = _item(tags=[f"arete_{i:026d}"])
        item.note.fields["Front"] = f"q{i}"
        nid = fake.add({"Front": f"q{i}", "Back": "old"}, deck="Elsewhere", tags=item.note.tags)
        item.note.nid = str(nid)
        items.append(item)
    lost = False

    def interrupt(action: str, params: dict):
        nonlocal lost
        subs = [a["action"] for a in params.get("actions", [])]
        if action == "multi" and "updateNoteFields" in subs and not lost:
            lost = True
            return True, TimeoutError()
        return None

    fake.interrupt = interrupt

    results = await _synced(fake).sync_notes(items)

    assert all(r.ok for r in results), [r.error for r in results]
    # the note-type read, the field writes twice, then the moves
    assert fake.requests["multi"] == 4
    assert fake.sub_actions["updateNoteFields"] == 6
    assert {fake.card_deck[c] for n in fake.notes.values() for c in n["cards"]} == {"D"}


@pytest.mark.asyncio
async def test_a_read_that_keeps_timing_out_fails_after_three_attempts():
    fake = FakeAnki()
    fake.interrupt = lambda action, params: (False, TimeoutError())

    with pytest.raises(TimeoutError):
        await _adapter(fake).get_deck_names()
    assert fake.requests["deckNames"] == 3


# ---------------------------------------------------------------------------
# The table: every action the adapter can send is classified
# ---------------------------------------------------------------------------


def _actions_in_source() -> set[str]:
    """Action names written in the adapter.

    From ``_invoke`` calls, write tuples and multi entries.
    """
    tree = ast.parse(inspect.getsource(anki_connect))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "_invoke":
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
        elif isinstance(node, ast.Tuple) and len(node.elts) == 2:
            name, params = node.elts
            if (
                isinstance(name, ast.Constant)
                and isinstance(name.value, str)
                and isinstance(params, (ast.Dict, ast.Name))
            ):
                names.add(name.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "action"
                    and isinstance(value, ast.Constant)
                ):
                    names.add(value.value)
    return names


def test_every_action_the_adapter_sends_is_in_the_retry_table():
    sent = _actions_in_source()
    assert {"addNote", "changeNoteType", "notesInfo", "multi", "setHomeDeck"} <= sent
    assert sent <= set(anki_connect._RESEND_SAFE) | {"multi"}


def test_the_retry_table_names_only_actions_the_adapter_sends():
    assert set(anki_connect._RESEND_SAFE) <= _actions_in_source()


def test_the_non_idempotent_actions_are_never_resent():
    assert anki_connect._RESEND_SAFE["addNote"] is False
    assert anki_connect._RESEND_SAFE["changeNoteType"] is False
