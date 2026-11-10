"""A sync that fails half-way must not send unstored ids or expose a note to prune.

Driven through the real `run_pipeline` with the in-memory Anki of
`test_pipeline_data_safety.py` standing in for the run's NoteSync.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from arete.application.config import AppConfig
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.pipeline import RunStats, run_pipeline
from arete.application.sync.vault_service import VaultService
from arete.domain.interfaces import AnkiBridge
from arete.infrastructure.persistence.cache import ContentCache
from tests.application.sync.test_pipeline_data_safety import ID_A1, FakeAnki, card, note_file
from tests.fakes.pipeline import syncing_through

LOG = logging.getLogger("test-crash-safety")


def run(root: Path, anki: FakeAnki, cache: ContentCache, **overrides) -> RunStats:
    fields: dict[str, Any] = {
        "root_input": root,
        "vault_root": root,
        "anki_media_dir": root / "media",
        "dry_run": False,
        "workers": 1,
        "queue_size": 10,
        "prune": False,
        "force": False,
    }
    fields.update(overrides)
    config = AppConfig.model_construct(**fields)
    vault = VaultService(root, cache, ignore_cache=False)
    parser = MarkdownParser(root, root / "media", ignore_cache=False, logger=LOG)
    bridge = cast(AnkiBridge, anki)
    with syncing_through(anki):
        return asyncio.run(
            run_pipeline(config, LOG, "run", vault, parser, bridge, cache, confirm_prune=True)
        )


@pytest.fixture
def vault(tmp_path):
    root = (tmp_path / "vault").resolve()
    root.mkdir()
    return root, ContentCache(tmp_path / "cache.db"), FakeAnki()


def test_a_failed_id_rewrite_sends_none_of_the_files_cards(vault, monkeypatch):
    """An id the vault could not store must never reach Anki, and prune keeps the file's notes."""
    root, cache, anki = vault
    path = root / "a.md"
    path.write_text(note_file(card(ID_A1)))
    run(root, anki, cache)
    assert len(anki.notes) == 1

    # An edit to the synced card, and a new card the pipeline must give an id to.
    head, tail = path.read_text().replace("Back: a\n", "Back: a edited\n", 1).rsplit("---\n", 1)
    path.write_text(head + "- Front: new\n  Back: x\n---\n" + tail)

    real_write = Path.write_text

    def read_only(self: Path, *args: Any, **kwargs: Any) -> int:
        if self == path:
            raise OSError("read-only file system")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", read_only)
    sent_before = len(anki.sent)
    stats = run(root, anki, cache, prune=True)

    assert anki.sent[sent_before:] == [], "no card of a file whose ids were not stored is sent"
    assert stats.total_errors >= 1
    assert len(anki.notes) == 1, "prune must keep the note the unwritable file still claims"


def test_prune_keeps_a_note_created_just_before_a_crash(vault, monkeypatch):
    """Prune keeps a note whose nid a crash kept out of the vault.

    The crash falls after the cache write and before the nid write-back. The next run hits
    the cache and sends nothing, so only the cached nid tells prune the note is claimed.
    """
    root, cache, anki = vault
    (root / "a.md").write_text(note_file(card(ID_A1)))

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("killed before write-back")

    with monkeypatch.context() as m:
        m.setattr(VaultService, "apply_updates", crash)
        with pytest.raises(RuntimeError, match="killed before write-back"):
            run(root, anki, cache)
    assert len(anki.notes) == 1
    assert "nid" not in (root / "a.md").read_text(), "the crash left the vault without the nid"

    stats = run(root, anki, cache, prune=True)

    assert stats.prune is not None and stats.prune.notes == []
    assert len(anki.notes) == 1, "the note the crash created is still the vault card's note"
