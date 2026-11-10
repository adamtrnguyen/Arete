"""After a sync, `_obsidian_source` holds the card's line in the file as the sync left it.

A sync writes nid and cid back into the file as lines inserted at the end of each card, so
every card below a written one moves. The line Anki stores must be the one on disk
afterwards, so "Open in Obsidian" lands on the card, and the next sync and a dry run plan
nothing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arete.application.sync.vault_service import VaultService
from arete.application.utils.text import parse_frontmatter
from tests.e2e.sync_harness import BACKENDS, make_backend, run_sync, sync_config


def _arete_id(n: int) -> str:
    return f"arete_01J{n:023d}"


def _file(n_cards: int) -> str:
    cards = "".join(
        f"- id: {_arete_id(600 + i)}\n  Front: Question {i}\n  Back: Answer {i}\n"
        for i in range(n_cards)
    )
    return f"---\narete: true\ndeck: Lines::Deck\nmodel: Basic\ncards:\n{cards}---\n\nBody.\n"


@pytest.fixture(params=BACKENDS)
def world(request, tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    backend = make_backend(request.param, tmp_path)
    yield tmp_path, vault, backend
    backend.close()


async def _sync(world, **config):
    root, vault, backend = world
    stats, updates = await run_sync(backend, sync_config(root, vault, backend, **config))
    assert stats.total_errors == 0 and all(u.ok for u in updates)
    return updates


def _lines_on_disk(md: Path) -> dict[str, int]:
    meta, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
    return {card["id"]: int(card["__line__"]) for card in meta["cards"]}


def _lines_in_anki(backend) -> dict[str, int]:
    out = {}
    for note in backend.notes():
        _vault, _path, line, arete_id = note.fields["_obsidian_source"].split("|")
        out[arete_id] = int(line)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("n_cards", [1, 3], ids=["one-card", "three-cards"])
async def test_the_stored_line_is_the_cards_line_after_write_back(world, n_cards):
    _, vault, backend = world
    md = vault / "lines.md"
    md.write_text(_file(n_cards), encoding="utf-8")
    before = _lines_on_disk(md)

    await _sync(world)

    after = _lines_on_disk(md)
    # The lines go in after each card: only a card below another one moves.
    assert (after != before) == (n_cards > 1)
    assert _lines_in_anki(backend) == after

    dry = await _sync(world, dry_run=True)
    again = await _sync(world)
    assert not any(u.changed for u in dry + again)
    assert _lines_in_anki(backend) == after


@pytest.mark.asyncio
async def test_a_crash_before_write_back_heals_with_the_right_line(world):
    _, vault, backend = world
    md = vault / "lines.md"
    md.write_text(_file(2), encoding="utf-8")

    class Crash(BaseException):
        pass

    with patch.object(VaultService, "apply_updates", side_effect=Crash):
        with pytest.raises(Crash):
            await _sync(world)
    assert _lines_in_anki(backend) == _lines_on_disk(md), "nothing written back yet"

    await _sync(world)

    assert _lines_in_anki(backend) == _lines_on_disk(md)
    dry = await _sync(world, dry_run=True)
    assert not any(u.changed for u in dry)
