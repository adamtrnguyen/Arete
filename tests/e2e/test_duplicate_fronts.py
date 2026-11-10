"""A new card whose first field another note of its type holds in its deck is refused.

One rule on every backend, decided in NoteSync: the first field compared through
`first_field_key` (`tests/domain/test_first_field_pins.py`), within the same deck and note
type. An identity match (the card's own nid or Arete id) is an update, never a refusal.
Two vault cards with one first field: the earlier in the vault is created, the later
refused, on every run, and never attached to the other's note.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter
from arete.infrastructure.persistence.cache import ContentCache
from tests.e2e.sync_harness import (
    BACKENDS,
    edit_vault,
    make_backend,
    run_sync,
    sync_config,
    vault_cards,
)

FIRST = "arete_01J00000000000000000000401"
SECOND = "arete_01J00000000000000000000402"


def _file(second_deck: str = "Dup::Deck", first_back: str = "First answer") -> str:
    return (
        "---\narete: true\ndeck: Dup::Deck\nmodel: Basic\ncards:\n"
        f"- id: {FIRST}\n  Front: Same question\n  Back: {first_back}\n"
        f"- id: {SECOND}\n  Front: Same question\n  Back: Second answer\n"
        f"  deck: {second_deck}\n"
        "---\n\nBody.\n"
    )


@pytest.fixture(params=BACKENDS)
def world(request, tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    backend = make_backend(request.param, tmp_path)
    yield tmp_path, vault, backend
    backend.close()


async def _sync(world, **config):
    root, vault, backend = world
    return await run_sync(backend, sync_config(root, vault, backend, **config))


def _recorded_as_synced(root: Path, md: Path) -> bool:
    st = md.stat()
    cache = ContentCache(db_path=root / "cache.db")
    return cache.get_file_meta_by_stat(md, st.st_mtime, st.st_size) is not None


@pytest.mark.asyncio
async def test_the_later_card_with_the_same_front_in_the_same_deck_is_refused(world):
    """Nothing is skipped silently: the file stays unsynced and prune keeps its note."""
    root, vault, backend = world
    (vault / "twins.md").write_text(_file(), encoding="utf-8")

    for run in range(2):  # the refusal holds on every run, prune confirmed on the second
        stats, updates = await _sync(world, prune=run == 1)
        assert not _recorded_as_synced(root, vault / "twins.md")
        first, second = sorted(updates, key=lambda u: u.source_index)
        [note] = backend.notes()
        assert first.ok and first.new_nid == str(note.nid)
        assert not second.ok and second.new_nid is None
        assert stats.total_errors >= 1
        error = second.error or ""
        assert "duplicate first field" in error
        assert "twins.md #2" in error and f"note {note.nid}" in error and "Dup::Deck" in error

    assert note.fields["Back"] == "<p>First answer</p>", "never attached to the other note"
    assert FIRST in note.tags and SECOND not in note.tags
    assert vault_cards(vault)["twins.md#2"]["nid"] is None


@pytest.mark.asyncio
async def test_the_earlier_card_in_the_vault_is_the_one_created(world):
    """Vault order decides: swapping the two cards' answers swaps nothing else."""
    _, vault, backend = world
    (vault / "twins.md").write_text(_file(first_back="Answer of the first card"), encoding="utf-8")

    await _sync(world)

    [note] = backend.notes()
    assert note.fields["Back"] == "<p>Answer of the first card</p>"
    assert FIRST in note.tags


@pytest.mark.asyncio
async def test_the_same_front_in_another_deck_is_created(world):
    _, vault, backend = world
    (vault / "twins.md").write_text(_file(second_deck="Dup::Other"), encoding="utf-8")

    stats, updates = await _sync(world)

    assert stats.total_errors == 0 and all(u.ok for u in updates)
    assert len(backend.notes()) == 2
    assert _recorded_as_synced(world[0], vault / "twins.md")


@pytest.mark.asyncio
async def test_a_card_found_by_its_own_id_is_an_update_not_a_duplicate(world):
    """The card lost its nid; its note holds the same front, so it is that card's note."""
    _, vault, backend = world
    (vault / "one.md").write_text(_file().split(f"- id: {SECOND}")[0] + "---\n", encoding="utf-8")
    await _sync(world)
    [note] = backend.notes()

    def drop_ids(text: str) -> str:
        meta, body = parse_frontmatter(text)
        meta["cards"][0].pop("anki")
        return rebuild_markdown_with_frontmatter(meta, body)

    edit_vault(vault / "one.md", drop_ids)
    assert vault_cards(vault)["one.md#1"]["nid"] is None

    stats, [update] = await _sync(world)

    assert stats.total_errors == 0 and update.ok and update.new_nid == str(note.nid)
    assert [n.nid for n in backend.notes()] == [note.nid]


@pytest.mark.asyncio
async def test_a_note_another_card_left_in_anki_refuses_the_new_card(world):
    """The note of a deleted file still holds the field: refused, never taken over."""
    _, vault, backend = world
    (vault / "old.md").write_text(_file().split(f"- id: {SECOND}")[0] + "---\n", encoding="utf-8")
    await _sync(world)
    [note] = backend.notes()
    (vault / "old.md").unlink()
    second_only = _file().replace(
        f"- id: {FIRST}\n  Front: Same question\n  Back: First answer\n", ""
    )
    (vault / "new.md").write_text(second_only, encoding="utf-8")

    stats, [update] = await _sync(world)

    assert not update.ok and stats.total_errors >= 1
    assert "duplicate first field" in (update.error or "") and f"note {note.nid}" in update.error
    assert [n.nid for n in backend.notes()] == [note.nid]
    assert FIRST in backend.notes()[0].tags and SECOND not in backend.notes()[0].tags
