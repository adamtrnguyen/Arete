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
