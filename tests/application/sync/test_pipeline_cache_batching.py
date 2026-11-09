"""The content cache is written once per consumer batch, and only after Anki accepted it.

Invariants pinned here:
- a card is recorded in the cache only after the bridge returned ok for it;
- one batch is one transaction: a failure records none of its rows, so those cards are
  sent again on the next sync instead of being skipped.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from arete.application.config import AppConfig
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import run_pipeline
from arete.application.sync.vault_service import VaultService
from arete.domain.interfaces import AnkiBridge
from arete.infrastructure.persistence.cache import ContentCache
from tests.application.sync.test_pipeline_data_safety import FakeAnki

LOG = logging.getLogger("test-cache-batching")


class SpyCache(ContentCache):
    def __init__(self, db_path: Path):
        """Count per-card and per-batch writes."""
        super().__init__(db_path)
        self.single_writes = 0
        self.batches: list[int] = []
        self.fail_batches = False

    def set_note(self, md_path, card_index, content_hash, note_json):
        self.single_writes += 1
        super().set_note(md_path, card_index, content_hash, note_json)

    def set_notes(self, rows):
        rows = list(rows)
        self.batches.append(len(rows))
        if self.fail_batches:
            raise OSError("disk full")
        super().set_notes(rows)


class CheckingAnki(FakeAnki):
    """Fails cards whose Front says so, and checks nothing was cached before it answered."""

    def __init__(self, cache: ContentCache) -> None:
        """Record any card already in the cache when it reaches Anki."""
        super().__init__()
        self.cache = cache
        self.cached_too_early: list[tuple[str, int]] = []
        self.calls = 0

    async def sync_notes(self, batch):
        self.calls += 1
        for wi in batch:
            if self.cache.get_hash(wi.source_file, wi.source_index) is not None:
                self.cached_too_early.append((wi.source_file.name, wi.source_index))
        out = await super().sync_notes(batch)
        for u in out:
            if u.note and "fail" in u.note.fields.get("Front", ""):
                u.ok, u.new_nid, u.new_cid, u.error = False, None, None, "rejected"
        return out


def write_vault(root: Path, files: int, cards: int, failing: set[tuple[int, int]] = frozenset()):
    for f in range(files):
        body = "".join(
            f"- id: arete_{f:013d}{c:013d}\n"
            f"  Front: {'fail' if (f, c) in failing else 'q'} {f}-{c}\n  Back: a\n"
            for c in range(cards)
        )
        (root / f"n{f}.md").write_text(f"---\narete: true\ndeck: D\ncards:\n{body}---\n")


def sync(root: Path, anki: FakeAnki, cache: ContentCache) -> None:
    fields: dict[str, Any] = {
        "root_input": root,
        "vault_root": root,
        "anki_media_dir": root / "media",
        "dry_run": False,
        "workers": 1,
        "queue_size": 1000,
        "prune": False,
        "force": False,
    }
    config = AppConfig.model_construct(**fields)
    vault = VaultService(root, cache, ignore_cache=False)
    parser = MarkdownParser(root, root / "media", ignore_cache=False, logger=LOG)
    asyncio.run(run_pipeline(config, LOG, "run", vault, parser, cast(AnkiBridge, anki), cache))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = (tmp_path / "vault").resolve()
    r.mkdir()
    return r


def test_one_cache_transaction_per_batch_not_per_card(root: Path, tmp_path: Path):
    write_vault(root, files=3, cards=40)
    cache = SpyCache(tmp_path / "cache.db")
    anki = CheckingAnki(cache)

    sync(root, anki, cache)

    assert cache.single_writes == 0, "every accepted card used to be its own commit"
    assert len(cache.batches) == anki.calls
    assert sum(cache.batches) == 120
    assert all(cache.get_hash(root / f"n{f}.md", c) for f in range(3) for c in range(1, 41))


def test_a_card_is_cached_only_after_anki_accepted_it(root: Path, tmp_path: Path):
    write_vault(root, files=2, cards=30, failing={(0, 3), (1, 29)})
    cache = SpyCache(tmp_path / "cache.db")
    anki = CheckingAnki(cache)

    sync(root, anki, cache)

    assert anki.cached_too_early == []
    assert cache.get_hash(root / "n0.md", 4) is None, "a rejected card must not be cached"
    assert cache.get_hash(root / "n1.md", 30) is None
    assert cache.get_hash(root / "n0.md", 3) is not None
    assert sum(cache.batches) == 58


def test_a_failed_cache_write_loses_rows_and_the_cards_are_sent_again(root: Path, tmp_path: Path):
    write_vault(root, files=2, cards=5)
    cache = SpyCache(tmp_path / "cache.db")
    cache.fail_batches = True
    anki = CheckingAnki(cache)

    sync(root, anki, cache)
    assert len(anki.sent) == 10
    assert all(cache.get_hash(root / f"n{f}.md", c) is None for f in range(2) for c in range(1, 6))

    cache.fail_batches = False
    sync(root, anki, cache)
    assert len(anki.sent) == 20, "nothing was recorded, so every card is checked again"

    sync(root, anki, cache)
    assert len(anki.sent) == 20, "once recorded, an unchanged vault sends nothing"


def test_set_notes_writes_all_rows_or_none(tmp_path: Path):
    cache = ContentCache(tmp_path / "cache.db")
    p = Path("/v/a.md")
    with pytest.raises(Exception):  # noqa: B017 - sqlite raises its own binding error type
        cache.set_notes([(p, 1, "h1", "{}"), (p, 2, "h2", "{}"), (p, 3, cast(str, object()), "{}")])
    assert cache.get_hash(p, 1) is None
    assert cache.get_hash(p, 2) is None

    cache.set_notes([(p, 1, "h1", "{}"), (p, 2, "h2", '{"x": 1}')])
    assert cache.get_note(p, 2) == ("h2", '{"x": 1}')
    cache.set_notes([])
