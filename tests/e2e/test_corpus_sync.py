"""Sync a corpus that mirrors the real vault's feature mix, end to end.

The fixtures under tests/fixtures/corpus are chosen from what a real vault actually
contains, measured over 978 notes and 3574 cards: three note types, nested decks, a
card-level deck override, display and inline math, an image embed, a callout, a code
fence, a dependency pair, and an accented filename. Content is neutral.

An optional second test runs the same invariants over a real vault when
ARETE_TEST_VAULT points at one. It parses only. It never writes to Anki.
"""

import collections
import logging
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from arete.application.sync.parser import MarkdownParser
from arete.application.sync.vault_service import VaultService
from arete.application.utils.media import build_filename_index
from arete.composition.orchestrator import execute_sync
from arete.infrastructure.persistence.cache import ContentCache
from tests.e2e.conftest import deck_of, open_collection

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"


@pytest.fixture
def corpus_vault(tmp_path: Path) -> Path:
    dest = tmp_path / "corpus_vault"
    shutil.copytree(CORPUS, dest)
    return dest


@pytest.mark.asyncio
async def test_the_whole_corpus_syncs_with_no_errors(
    corpus_vault: Path, anki_base: Path, make_config
):
    """Every note type, deck shape and markdown feature the real vault uses."""
    stats = await execute_sync(make_config(corpus_vault))

    assert stats.total_errors == 0
    assert stats.total_generated == 14, "10 notes, 14 cards"
    assert stats.total_imported == 14

    col = open_collection(anki_base)
    try:
        nids = col.find_notes("")
        assert len(nids) == 14

        models = collections.Counter(col.get_note(n).note_type()["name"] for n in nids)
        assert models["Basic"] == 12
        assert models["Cloze"] == 1
        assert models["Basic with Extra"] == 1

        decks = {deck_of(col, n) for n in nids}
        assert "Corpus::Basic" in decks
        assert "Corpus::CardLevel" in decks, "a card-level deck must override its file"
        assert "Corpus::FileLevel" in decks
        assert "Default" not in decks, "nothing may fall back to Default"
    finally:
        col.close()


@pytest.mark.asyncio
async def test_an_image_embed_reaches_the_anki_media_folder(corpus_vault: Path, make_config):
    """An embed must copy the file, or the card renders a broken image."""
    config = make_config(corpus_vault)
    await execute_sync(config)

    copied = list(Path(config.anki_media_dir).glob("*.png"))
    assert copied, f"no media copied into {config.anki_media_dir}"
    assert copied[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_the_corpus_is_stable_across_runs(corpus_vault: Path, anki_base: Path, make_config):
    """A second pass over an unchanged corpus adds nothing and changes nothing."""
    await execute_sync(make_config(corpus_vault))
    col = open_collection(anki_base)
    try:
        first = {n: col.get_note(n).fields[0] for n in col.find_notes("")}
    finally:
        col.close()

    await execute_sync(make_config(corpus_vault))
    col = open_collection(anki_base)
    try:
        second = {n: col.get_note(n).fields[0] for n in col.find_notes("")}
    finally:
        col.close()

    assert first == second, "a second sync must not add, move or rewrite anything"


REAL_VAULT = os.getenv("ARETE_TEST_VAULT")


@pytest.mark.skipif(not REAL_VAULT, reason="set ARETE_TEST_VAULT to a vault to run this")
def test_a_real_vault_parses_and_holds_its_invariants():
    """Read-only invariants over a real vault. Touches no Anki and writes no vault file.

    This is the check that found three duplicate Arete ids in a 978-note vault.
    """
    vault = Path(REAL_VAULT)  # type: ignore[arg-type]
    tmp = Path(tempfile.mkdtemp())
    log = logging.getLogger("corpus")
    cache = ContentCache(db_path=tmp / "cache.db")
    service = VaultService(vault, cache, ignore_cache=True)
    parser = MarkdownParser(
        vault, tmp / "media", ignore_cache=True, default_deck="Default", logger=log
    )

    errors: list[str] = []
    ids: collections.Counter[str] = collections.Counter()
    decks: collections.Counter[str] = collections.Counter()
    cards = 0

    for path, meta, _fresh in service.scan_for_compatible_files():
        try:
            notes, _skipped, _inv = parser.parse_file(
                path, meta, cache, build_filename_index(vault, log), False
            )
        except Exception as e:  # noqa: BLE001 - the point is to report, not to raise
            errors.append(f"{path.name}: {type(e).__name__}: {e}")
            continue
        cards += len(notes)
        for note in notes:
            decks[note.deck] += 1
            ids.update(t for t in note.tags if t.startswith("arete_"))

    assert cards, "the vault produced no cards at all"
    assert not errors, f"{len(errors)} notes failed to parse: {errors[:5]}"
    assert decks.get("Default", 0) == 0, "cards fell back to the Default deck"
    duplicates = {i: n for i, n in ids.items() if n > 1}
    assert not duplicates, f"an Arete id must identify one card: {duplicates}"
