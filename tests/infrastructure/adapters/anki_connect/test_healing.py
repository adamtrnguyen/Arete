"""Reconcile over the real HTTP transport: NoteSync through the AnkiConnect adapter's client.

The decisions themselves are tested once, against the in-memory note store
(`tests/application/sync/test_note_sync.py`). Here `respx` answers the adapter's httpx
requests with the in-memory AnkiConnect, so the requests are real JSON over the adapter's
real client: a heal by content, a heal by the id tag, and a refused add.
"""

import json
from pathlib import Path

import pytest
import respx
from httpx import Request, Response

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AnkiNote, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

from .fake_anki import FakeAnki

URL = "http://mock-anki:8765"
ID = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N8"


def _item(front: str = "<p>Front</p>", nid: int | None = None) -> WorkItem:
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": front, "Back": "Back"},
        tags=["tag1", ID],
        start_line=1,
        end_line=10,
        source_file=Path("test.md"),
        source_index=1,
        nid=None if nid is None else str(nid),
        arete_id=ID,
    )
    return WorkItem(note=note, source_file=Path("test.md"), source_index=1)


def _serve(fake: FakeAnki) -> None:
    """Answer every request to URL as the in-memory AnkiConnect does."""

    def answer(request: Request) -> Response:
        return Response(200, json=fake._handle(json.loads(request.content)))

    respx.post(URL).mock(side_effect=answer)


async def _sync(item: WorkItem):
    adapter = AnkiConnectAdapter(url=URL)
    try:
        [result] = await NoteSync(adapter).sync_notes([item])
    finally:
        await adapter.close()
    return result


@pytest.mark.asyncio
@respx.mock
async def test_a_legacy_note_is_healed_by_content_over_http():
    fake = FakeAnki()
    legacy = fake.add({"Front": "<b>front</b>", "Back": "old"})
    _serve(fake)

    result = await _sync(_item())

    assert result.ok and result.new_nid == str(legacy)
    assert fake.notes[legacy]["fields"]["Back"] == "Back"
    assert ID in fake.notes[legacy]["tags"]
    assert len(fake.notes) == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_dangling_nid_is_healed_by_the_id_tag_over_http():
    fake = FakeAnki()
    own = fake.add({"Front": "<p>Reworded</p>"}, deck="Default", tags=[ID])
    _serve(fake)

    result = await _sync(_item(nid=1111111111111))

    assert result.ok and result.new_nid == str(own)
    assert result.new_cid == str(fake.notes[own]["cards"][0])
    assert fake.notes[own]["fields"]["Front"] == "<p>Front</p>"


@pytest.mark.asyncio
@respx.mock
async def test_a_refused_add_fails_the_card_over_http():
    fake = FakeAnki()
    _serve(fake)

    result = await _sync(_item(front=" "))

    assert not result.ok
    assert "cannot create note because it is empty" in (result.error or "")
    assert fake.notes == {}
