"""A plain note is read once, then skipped by its stat until it changes.

The stat cache used to hold Arete files only, so every plain note was read again on
every sync to look for `arete:`. A note that is not an Arete file is now remembered as
such, keyed by its mtime and size, and any change to the file makes the scan read it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arete.application.sync.vault_service import VaultService
from arete.infrastructure.persistence.cache import ContentCache
from tests.application.sync.test_pipeline_cache_batching import CheckingAnki, sync

ARETE = "---\narete: true\ndeck: D\ncards:\n- id: arete_{n:026d}\n  Front: q{n}\n  Back: a\n---\n"
PLAIN = "---\ntags: [a]\n---\n# Plain {n}\n" + "body text\n" * 10


@pytest.fixture
def reads(monkeypatch):
    """Count read_text calls per file name."""
    counts: dict[str, int] = {}
    original = Path.read_text

    def counting(self, *args, **kwargs):
        counts[self.name] = counts.get(self.name, 0) + 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    return counts


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = (tmp_path / "vault").resolve()
    r.mkdir()
    for n in range(3):
        (r / f"plain{n}.md").write_text(PLAIN.format(n=n))
    (r / "card.md").write_text(ARETE.format(n=1))
    return r


def scan(service: VaultService) -> list[str]:
    found = [p.name for p, _meta, _fresh in service.scan_for_compatible_files()]
    service.record_synced(service.root / name for name in found)
    return found


def test_an_unchanged_plain_note_is_not_read_again(root: Path, tmp_path: Path, reads):
    service = VaultService(root, ContentCache(tmp_path / "cache.db"))
    assert scan(service) == ["card.md"]
    assert reads.get("plain0.md") == 1

    reads.clear()
    assert scan(service) == ["card.md"]
    assert not any(name.startswith("plain") for name in reads), reads


def test_a_note_that_gains_arete_frontmatter_is_picked_up_next_scan(root: Path, tmp_path: Path):
    service = VaultService(root, ContentCache(tmp_path / "cache.db"))
    assert scan(service) == ["card.md"]

    (root / "plain1.md").write_text(ARETE.format(n=2))
    assert sorted(scan(service)) == ["card.md", "plain1.md"]


def test_a_same_size_edit_with_a_new_mtime_is_read(root: Path, tmp_path: Path):
    """Only the stat keys the entry, so an edit that keeps the size must move the mtime."""
    service = VaultService(root, ContentCache(tmp_path / "cache.db"))
    note = root / "plain2.md"
    before = note.stat()
    assert scan(service) == ["card.md"]

    gained = "---\narete: true\ndeck: D\ncards:\n- Front: q\n  Back: a\n---\n"
    padded = gained + "x" * (before.st_size - len(gained))
    assert len(padded) == before.st_size
    note.write_text(padded)
    os.utime(note, (before.st_atime, before.st_mtime + 2))
    assert note.stat().st_size == before.st_size

    assert "plain2.md" in scan(service)


def test_force_ignores_the_entry(root: Path, tmp_path: Path, reads):
    cache = ContentCache(tmp_path / "cache.db")
    scan(VaultService(root, cache))
    reads.clear()
    scan(VaultService(root, cache, ignore_cache=True))
    assert reads.get("plain0.md") == 1


def test_an_unreadable_arete_file_is_reported_on_every_scan(root: Path, tmp_path: Path):
    """A broken Arete file blocks prune; remembering it as 'not Arete' would unblock it."""
    (root / "broken.md").write_text("---\narete: true\ncards: [\n---\n")
    service = VaultService(root, ContentCache(tmp_path / "cache.db"))
    for _ in range(2):
        scan(service)
        assert [p.name for p, _ in service.unreadable] == ["broken.md"]


def test_a_gained_card_reaches_anki_on_the_next_sync(root: Path, tmp_path: Path):
    cache = ContentCache(tmp_path / "cache.db")
    anki = CheckingAnki(cache)
    sync(root, anki, cache)
    assert [name for name, _ in anki.sent] == ["card.md"]

    (root / "plain0.md").write_text(ARETE.format(n=7))
    sync(root, anki, cache)
    assert [name for name, _ in anki.sent] == ["card.md", "plain0.md"]

    sync(root, anki, cache)
    assert len(anki.sent) == 2
