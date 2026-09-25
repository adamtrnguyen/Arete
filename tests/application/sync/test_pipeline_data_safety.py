"""The sync pipeline must never lose a note, a card, or its review history.

Each test is a reproduction from the 2026-09-25 bug hunt, driven through the real
`run_pipeline` with an in-memory Anki. The fake mirrors the direct backend's prune view
(`get_notes_in_deck` -> {str(nid): nid}).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from arete.application.config import AppConfig
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import run_pipeline
from arete.application.sync.vault_service import VaultService
from arete.domain.models import UpdateItem
from arete.infrastructure.persistence.cache import ContentCache

LOG = logging.getLogger("test-data-safety")


class FakeAnki:
    def __init__(self) -> None:
        """Start empty and succeeding; set `fail` to make every sync_notes call fail."""
        self.notes: dict[int, str] = {}  # nid -> deck
        self.sent: list[tuple[str, int]] = []
        self.fail = False
        self._next = 1000

    async def sync_notes(self, batch):
        out = []
        for wi in batch:
            note = wi.note
            self.sent.append((wi.source_file.name, wi.source_index))
            if self.fail:
                out.append(
                    UpdateItem(
                        source_file=wi.source_file,
                        source_index=wi.source_index,
                        new_nid=None,
                        new_cid=None,
                        ok=False,
                        error="anki unavailable",
                        note=note,
                    )
                )
                continue
            nid = int(note.nid) if note.nid and int(note.nid) in self.notes else None
            if nid is None:
                self._next += 1
                nid = self._next
            self.notes[nid] = note.deck
            out.append(
                UpdateItem(
                    source_file=wi.source_file,
                    source_index=wi.source_index,
                    new_nid=str(nid),
                    new_cid=str(nid),
                    ok=True,
                    note=note,
                )
            )
        return out

    async def get_deck_names(self):
        return sorted(set(self.notes.values()) | {"Default"})

    async def get_notes_in_deck(self, deck):
        return {str(nid): nid for nid, d in self.notes.items() if d == deck}

    async def delete_notes(self, ids):
        for i in ids:
            self.notes.pop(i, None)

    async def delete_decks(self, names):
        pass


def card(cid: str, front: str = "q", back: str = "a") -> str:
    return f"- id: {cid}\n  Front: {front}\n  Back: {back}\n"


def note_file(*cards: str, deck: str = "D") -> str:
    return f"---\ndeck: {deck}\ncards:\n{''.join(cards)}---\n"


def edit(path: Path, card_id: str) -> None:
    """Change one card's Back by a different number of bytes, as a real edit does."""
    text = path.read_text()
    head, tail = text.split(f"id: {card_id}\n", 1)
    path.write_text(head + f"id: {card_id}\n" + tail.replace("Back: a\n", "Back: a edited\n", 1))


def sync(root: Path, anki: FakeAnki, cache: ContentCache, **overrides) -> None:
    fields = {
        "root_input": root,
        "vault_root": root,
        "anki_media_dir": root / "media",
        "dry_run": False,
        "workers": 1,
        "queue_size": 10,
        "prune": False,
        "force": True,
    }
    fields.update(overrides)
    config = AppConfig.model_construct(**fields)
    vault = VaultService(root, cache, ignore_cache=False)
    parser = MarkdownParser(root, root / "media", ignore_cache=False, logger=LOG)
    asyncio.run(run_pipeline(config, LOG, "run", vault, parser, anki, cache))


@pytest.fixture
def vault(tmp_path):
    root = (tmp_path / "vault").resolve()
    root.mkdir()
    return root, ContentCache(tmp_path / "cache.db"), FakeAnki()


def test_prune_keeps_notes_created_in_the_same_run(vault):
    """New cards were inventoried with nid=None, so prune deleted what it had just made."""
    root, cache, anki = vault
    (root / "a.md").write_text(note_file(card("arete_A1")))
    sync(root, anki, cache, prune=True)
    assert len(anki.notes) == 1


def test_prune_keeps_the_note_of_a_card_that_fails_validation(vault):
    """Emptying a Back skips the card; its existing note must survive a prune."""
    root, cache, anki = vault
    (root / "a.md").write_text(note_file(card("arete_A1"), card("arete_A2", back="answer two")))
    sync(root, anki, cache)
    assert len(anki.notes) == 2

    text = (root / "a.md").read_text().replace("Back: answer two", "Back: ''")
    (root / "a.md").write_text(text)
    sync(root, anki, cache, prune=True)
    assert len(anki.notes) == 2


def test_prune_refuses_while_a_file_cannot_be_read(vault):
    """A YAML error hides a file's note ids, so nothing can be proven orphaned."""
    root, cache, anki = vault
    (root / "a.md").write_text(note_file(card("arete_A1")))
    (root / "b.md").write_text(note_file(card("arete_B1")))
    sync(root, anki, cache)
    assert len(anki.notes) == 2

    broken = (root / "b.md").read_text().replace("deck: D", "deck: D\ndeck: E")
    (root / "b.md").write_text(broken)
    sync(root, anki, cache, prune=True)
    assert len(anki.notes) == 2


def test_a_failed_sync_is_retried_next_run(vault):
    """The hash was cached before the sync succeeded, so a failed card never reached Anki."""
    root, cache, anki = vault
    (root / "a.md").write_text(note_file(card("arete_A1"), card("arete_A2")))
    anki.fail = True
    sync(root, anki, cache)
    anki.fail = False
    # an edit to card 2 makes the file fresh, so the per-card hash decides what is sent
    edit(root / "a.md", "arete_A2")
    sync(root, anki, cache)
    assert len(anki.notes) == 2


def test_a_dry_run_leaves_every_card_pending(vault):
    """--dry-run filled the hash cache, so the next real run skipped the previewed cards."""
    root, cache, anki = vault
    (root / "a.md").write_text(note_file(card("arete_A1"), card("arete_A2")))
    sync(root, anki, cache, dry_run=True)
    assert anki.notes == {}
    edit(root / "a.md", "arete_A2")
    sync(root, anki, cache)
    assert len(anki.notes) == 2


def test_write_back_matches_the_card_by_id_not_position(vault):
    """A card inserted above during a sync must not receive the other card's nid."""
    import yaml

    from arete.domain.models import AnkiNote

    root, cache, _ = vault
    path = root / "a.md"
    # parsed while arete_A was card #1 ...
    note = AnkiNote(
        model="Basic",
        deck="D",
        fields={},
        tags=[],
        start_line=0,
        end_line=0,
        source_file=path,
        source_index=1,
        arete_id="arete_A",
    )
    # ... then the user inserted a card at the top before the write-back ran
    path.write_text(note_file(card("arete_NEW"), card("arete_A")))
    VaultService(root, cache).apply_updates(
        [
            UpdateItem(
                source_file=path, source_index=1, new_nid="555", new_cid="556", ok=True, note=note
            )
        ]
    )
    cards = yaml.safe_load(path.read_text().split("---")[1])["cards"]
    by_id = {c["id"]: c for c in cards}
    assert str(by_id["arete_A"]["anki"]["nid"]) == "555"
    assert "anki" not in by_id["arete_NEW"]
