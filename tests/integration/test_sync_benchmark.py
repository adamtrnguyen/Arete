"""Wall time of a sync where nothing changed, 7,000 notes, against the Docker Anki.

Every sync compares every card with Anki, so this is the cost of an ordinary run. Opt-in
(ARETE_BENCH=1): it creates 7,000 notes first. The times are printed, not asserted; the
owner sets the limit.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from arete.application.sync.note_sync import NoteSync
from arete.domain.models import AnkiNote, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

N = 7000
BATCH = 50
DECK = "Bench::Unchanged"

pytestmark = pytest.mark.skipif(
    os.getenv("ARETE_BENCH") != "1", reason="set ARETE_BENCH=1 to run the 7,000-note benchmark"
)


def _item(i: int) -> WorkItem:
    arete_id = f"arete_01B{i:023d}"
    note = AnkiNote(
        model="Basic",
        deck=DECK,
        fields={"Front": f"<p>Benchmark question {i}</p>", "Back": f"<p>Answer {i}</p>"},
        tags=["bench", arete_id],
        start_line=1,
        end_line=1,
        source_file=Path("bench.md"),
        source_index=i,
        arete_id=arete_id,
    )
    return WorkItem(note=note, source_file=note.source_file, source_index=i)


async def _sync_all(adapter: AnkiConnectAdapter, items: list[WorkItem], prefetch: bool):
    sync = NoteSync(adapter)
    if prefetch:
        await sync.prefetch([int(i.note.nid or 0) for i in items], {"Basic"})
    results = []
    for start in range(0, len(items), BATCH):
        results += await sync.sync_notes(items[start : start + BATCH])
    return results


@pytest.mark.asyncio
async def test_benchmark_a_sync_of_7000_unchanged_notes(anki_url, capsys):
    adapter = AnkiConnectAdapter(url=anki_url)
    items = [_item(i) for i in range(N)]
    try:
        started = time.perf_counter()
        created = await _sync_all(adapter, items, prefetch=False)
        create_s = time.perf_counter() - started
        assert all(r.ok for r in created), [r.error for r in created if not r.ok][:3]
        for item, result in zip(items, created, strict=True):
            item.note.nid, item.note.cid = result.new_nid, result.new_cid

        started = time.perf_counter()
        compared = await _sync_all(adapter, items, prefetch=True)
        compare_s = time.perf_counter() - started
    finally:
        await adapter.close()

    assert all(r.ok and r.changed is False for r in compared)
    with capsys.disabled():
        print(f"\n[bench] create {N} notes: {create_s:.1f} s; unchanged sync: {compare_s:.1f} s")
