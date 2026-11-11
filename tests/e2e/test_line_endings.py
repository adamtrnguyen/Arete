"""A vault stored with CRLF line endings syncs to the same Anki state as with LF.

Git and editors on Windows may store notes with CRLF. What reaches Anki (fields, tags,
decks, `_obsidian_source` lines) and the ids written back must not depend on that, so a
card synced from Windows and from macOS ends up identical.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from arete.application.utils.text import parse_frontmatter
from tests.e2e.sync_harness import make_backend, run_sync, snapshot, sync_config

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"


def _corpus(root: Path, eol: str) -> Path:
    vault = root / "vault"
    shutil.copytree(CORPUS, vault)
    for md in vault.rglob("*.md"):
        text = md.read_bytes().decode("utf-8").replace("\r\n", "\n")
        md.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return vault


async def _synced_twice(root: Path, eol: str) -> dict:
    vault = _corpus(root, eol)
    backend = make_backend("fake", root)
    runs = []
    try:
        for _ in range(2):
            _, updates = await run_sync(backend, sync_config(root, vault, backend))
            runs.append(updates)
        return snapshot(backend, vault, runs)
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_a_crlf_vault_syncs_exactly_as_its_lf_copy(tmp_path: Path):
    lf = await _synced_twice(tmp_path / "lf", "\n")
    crlf = await _synced_twice(tmp_path / "crlf", "\r\n")
    assert lf["notes"], "the corpus synced something"
    assert crlf == lf


def test_a_crlf_vault_keeps_crlf_after_the_ids_are_written(tmp_path: Path):

    asyncio.run(_synced_twice(tmp_path, "\r\n"))
    for md in (tmp_path / "vault").rglob("*.md"):
        raw = md.read_bytes()
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{md.name} has a bare LF"


@pytest.mark.parametrize("md", sorted(CORPUS.rglob("*.md")), ids=lambda p: p.name)
def test_the_frontmatter_of_a_crlf_note_parses_as_its_lf_copy(md: Path):
    text = md.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert parse_frontmatter(text.replace("\n", "\r\n"))[0] == parse_frontmatter(text)[0]
