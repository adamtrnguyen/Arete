"""The attachment index: same entries as a plain os.walk + is_file + resolve, built lazily."""

import logging
import os
from pathlib import Path

import pytest

from arete.application.utils import media
from arete.application.utils.media import FilenameIndex, build_filename_index
from arete.domain.constants import MEDIA_DIR_NAMES
from arete.infrastructure.persistence.cache import ContentCache
from tests.application.sync.test_pipeline_cache_batching import CheckingAnki, sync

LOG = logging.getLogger("test-media-index")


def reference_index(vault_root: Path) -> dict[str, list[Path]]:
    """The straightforward walk: every entry stat-ed and resolved one by one."""
    idx: dict[str, list[Path]] = {}
    for root in MEDIA_DIR_NAMES:
        base = vault_root / root
        if base.is_dir():
            for dirpath, _, filenames in os.walk(base):
                for f in filenames:
                    p = Path(dirpath) / f
                    if p.is_file():
                        idx.setdefault(p.name, []).append(p.resolve())
    return idx


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    (outside / "dir").mkdir(parents=True)
    (outside / "target.png").write_bytes(b"t")
    (outside / "dir" / "inside.png").write_bytes(b"i")
    for d in ("attachments", "assets"):
        for k in range(3):
            sub = vault / d / f"s{k}" / "deep"
            sub.mkdir(parents=True)
            (sub / f"{d}{k}.png").write_bytes(b"x")
            (vault / d / f"s{k}" / "same.png").write_bytes(b"y")
    att = vault / "attachments"
    os.symlink(outside / "target.png", att / "alias.png")  # link name differs from target
    os.symlink(outside / "missing.png", att / "broken.png")
    os.symlink(outside / "dir", att / "linkdir")  # a symlinked folder is not entered
    os.symlink(outside / "dir", vault / "media")  # a symlinked media root is
    if hasattr(os, "mkfifo"):  # no FIFO can exist on Windows; on POSIX the walk skips it
        os.mkfifo(att / "pipe.png")
    (vault / "note.png").write_bytes(b"n")  # outside every media folder
    return vault


def test_the_index_matches_the_reference_walk(tree: Path):
    idx = build_filename_index(tree, LOG)
    assert idx == reference_index(tree)
    assert idx["alias.png"] == [(tree.parent / "outside" / "target.png").resolve()]
    assert len(idx["same.png"]) == 6, "order matters: the first existing candidate wins"
    assert "broken.png" not in idx and "pipe.png" not in idx and "note.png" not in idx
    assert "inside.png" in idx


def test_the_index_matches_through_a_symlinked_vault_path(tree: Path):
    link = tree.parent / "vault-link"
    os.symlink(tree, link)
    assert build_filename_index(link, LOG) == reference_index(link)


def test_the_lazy_index_walks_once_and_only_when_asked(tree: Path, monkeypatch):
    walks = []
    monkeypatch.setattr(media, "build_filename_index", lambda root, log: walks.append(root) or {})
    idx = FilenameIndex(tree, LOG)
    assert walks == []
    idx.ensure_built()
    idx.ensure_built()
    assert walks == [tree]


@pytest.fixture
def counted_walks(monkeypatch):
    """Count index builds by the media-folder list they iterate, however they are reached."""
    walks: list[int] = []

    class Counting(tuple):
        def __iter__(self):
            walks.append(1)
            return super().__iter__()

    monkeypatch.setattr(media, "MEDIA_DIR_NAMES", Counting(MEDIA_DIR_NAMES))
    return walks


def _vault_with(tmp_path: Path, back: str) -> Path:
    root = (tmp_path / "v").resolve()
    (root / "attachments" / "sub").mkdir(parents=True)
    (root / "attachments" / "sub" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (root / "n.md").write_text(
        f"---\narete: true\ndeck: D\ncards:\n- id: arete_{1:026d}\n  Front: q\n  Back: {back}\n---\n"
    )
    return root


def test_a_sync_without_images_never_walks_the_attachments(tmp_path: Path, counted_walks):
    root = _vault_with(tmp_path, "plain answer")
    cache = ContentCache(tmp_path / "c.db")
    anki = CheckingAnki(cache)
    sync(root, anki, cache)
    assert len(anki.sent) == 1
    assert counted_walks == []


def test_an_image_found_only_through_the_index_still_resolves(tmp_path: Path, counted_walks):
    root = _vault_with(tmp_path, "'![[pic.png]]'")
    cache = ContentCache(tmp_path / "c.db")
    anki = CheckingAnki(cache)
    sync(root, anki, cache)
    assert counted_walks == [1]
    assert (root / "media" / "pic.png").read_bytes().startswith(b"\x89PNG")
    _hash, note_json = cache.get_note(root / "n.md", 1) or ("", "")
    assert "pic.png" in note_json
