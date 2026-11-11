"""One NoteSync batch against real Anki: one read per batch, writes only where Anki differs.

The writes of several notes travel in one ``multi`` request, so this checks the add-on's
real ``multi`` and ``getHomeDecks`` replies, not a fake's.
"""

from collections import Counter
from pathlib import Path

import pytest
import requests

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AnkiNote, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


def _item(i: int, deck: str, nid: int | None = None, back: str = "b", extra_tag: bool = False):
    tags = [f"arete_batched{i:020d}"] + (["extra"] if extra_tag else [])
    note = AnkiNote(
        model="Basic",
        deck=deck,
        fields={"Front": f"batched front {i}", "Back": back},
        tags=tags,
        start_line=1,
        nid=str(nid) if nid else None,
        source_file=Path("batched.md"),
        source_index=i,
        arete_id=tags[0],
    )
    return WorkItem(note=note, source_file=Path("batched.md"), source_index=i)


def _call(url: str, action: str, **params):
    reply = requests.post(url, json={"action": action, "version": 6, "params": params}).json()
    assert reply["error"] is None, reply
    return reply["result"]


@pytest.mark.asyncio
async def test_one_batch_writes_only_what_differs(anki_url, setup_anki, caplog):
    deck = setup_anki
    moved = f"{deck}::Moved"
    adapter = AnkiConnectAdapter(url=anki_url)
    try:
        created = await NoteSync(adapter).sync_notes([_item(i, deck) for i in range(4)])
        assert all(r.ok and r.new_cid for r in created), [r.error for r in created]
        nids = [int(r.new_nid or 0) for r in created]

        sent: Counter[str] = Counter()
        real_request = adapter._http_request

        async def counting(payload: dict) -> dict:
            sent[payload["action"]] += 1
            for sub in payload["params"].get("actions", []):
                sent[f"multi:{sub['action']}"] += 1
            return await real_request(payload)

        adapter._http_request = counting  # type: ignore[method-assign]
        results = await NoteSync(adapter).sync_notes(
            [
                _item(0, deck, nids[0], back="changed"),
                _item(1, moved, nids[1]),
                _item(2, deck, nids[2], extra_tag=True),
                _item(3, deck, nids[3]),
            ]
        )
    finally:
        await adapter.close()

    assert all(r.ok for r in results), [r.error for r in results]
    # A getHomeDecks lost to the add-on's dropped connections files every card (the safe side).
    moved_anyway = any("getHomeDecks failed" in r.getMessage() for r in caplog.records)
    assert sent == {
        "multi": 2,  # the note types with the deck names; the writes, one step each note
        "multi:getModelInfo": 1,
        "multi:deckNames": 1,
        "createDeck": 1,  # the new subdeck
        "notesInfo": 1,
        "getHomeDecks": 1,
        "multi:updateNoteFields": 1,
        "multi:setHomeDeck": 4 if moved_anyway else 1,
        "multi:addTags": 1,
    }

    infos = _call(anki_url, "notesInfo", notes=nids)
    assert infos[0]["fields"]["Back"]["value"] == "changed"
    assert "extra" in infos[2]["tags"]
    decks = _call(anki_url, "getDecks", cards=[c for info in infos for c in info["cards"]])
    assert decks == {
        deck: infos[0]["cards"] + infos[2]["cards"] + infos[3]["cards"],
        moved: infos[1]["cards"],
    }
