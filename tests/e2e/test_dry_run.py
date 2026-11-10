"""A dry run decides as a sync does, through NoteSync, and writes nothing anywhere.

It reads Anki to say what a sync would overwrite, create, adopt or refuse. Neither the
collection's tables nor the vault's bytes change.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.sync_harness import (
    BACKENDS,
    Backend,
    CollectionBackend,
    FakeBackend,
    make_backend,
    run_sync,
    sync_config,
)

ID = "arete_01J00000000000000000000301"
CARD = f"""---
arete: true
deck: Dry::Home
model: Basic
cards:
- id: {ID}
  Front: What does a dry run write?
  Back: Nothing.
---

Body.
"""
TABLES = ("notes", "cards", "decks", "notetypes", "fields", "revlog", "tags")


def _anki_state(backend: Backend) -> Any:
    """Every row of the tables a sync could touch, or the fake's whole state."""
    if isinstance(backend, FakeBackend):
        fake = backend.fake
        return copy.deepcopy((fake.notes, fake.card_deck, fake.decks, fake.models))
    assert isinstance(backend, CollectionBackend)
    with backend.collection() as col:
        return {t: col.db.all(f"select * from {t} order by 1") for t in TABLES}


def _vault_bytes(vault: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(vault.rglob("*.md"))}


@pytest.fixture(params=BACKENDS)
def world(request, tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "card.md").write_text(CARD, encoding="utf-8")
    backend = make_backend(request.param, tmp_path)
    yield tmp_path, vault, backend
    backend.close()


@pytest.mark.asyncio
async def test_a_dry_run_after_an_anki_edit_reports_the_overwrite_and_writes_nothing(world, caplog):
    root, vault, backend = world
    await run_sync(backend, sync_config(root, vault, backend))
    [note] = backend.notes()
    backend.set_field(note.nid, "Back", "edited in Anki")
    backend.add_tag(note.nid, "anki_only")
    (vault / "new.md").write_text(
        CARD.replace(ID, "arete_01J00000000000000000000302").replace("Dry::Home", "Dry::New"),
        encoding="utf-8",
    )
    anki_before, vault_before = _anki_state(backend), _vault_bytes(vault)

    with caplog.at_level(logging.INFO):
        stats, updates = await run_sync(
            backend, sync_config(root, vault, backend, dry_run=True, force=True)
        )

    report = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[dry-run]")]
    overwrite = [line for line in report if "would overwrite" in line]
    assert len(overwrite) == 1, report
    assert f"nid={note.nid}" in overwrite[0]
    assert "fields Back" in overwrite[0] and "-anki_only" in overwrite[0]
    assert any("would create new.md #1 in deck 'Dry::New'" in line for line in report)
    assert any("would create deck 'Dry::New'" in line for line in report)
    assert stats.total_errors == 0 and all(u.ok for u in updates)
    assert _anki_state(backend) == anki_before
    assert _vault_bytes(vault) == vault_before
