"""End-to-end sync against a real Anki collection, with nothing external running.

Every test here goes through the same path a user does: an AppConfig, the real
composition root, the real pipeline, a real adapter, and a real Anki SQLite file.
The only thing faked is the location, which is `tmp_path`.
"""

from pathlib import Path

import pytest

from arete.composition.orchestrator import execute_sync
from tests.e2e.conftest import deck_of, open_collection


@pytest.mark.asyncio
async def test_one_card_lands_in_the_deck_its_note_declares(
    single_card_vault: Path, anki_base: Path, make_config
):
    """The smallest end-to-end claim: one card, in the deck the frontmatter names.

    A card that silently lands in `Default` is the failure this pins down. The bug
    that motivated this suite put 526 notes there.
    """
    stats = await execute_sync(make_config(single_card_vault))

    assert stats.total_generated == 1
    assert stats.total_imported == 1
    assert stats.total_errors == 0

    col = open_collection(anki_base)
    try:
        nids = col.find_notes("")
        assert len(nids) == 1, "exactly one note should exist"
        note = col.get_note(nids[0])
        assert "core components of a Transformer block" in note.fields[0]
        assert deck_of(col, nids[0]) == "AI::Deep Learning::Transformers"
    finally:
        col.close()


@pytest.mark.asyncio
async def test_sync_writes_the_anki_id_back_into_the_note(
    single_card_vault: Path, anki_base: Path, make_config
):
    """Without the write-back the next run cannot tell the card from a new one."""
    md = single_card_vault / "Transformer.md"
    assert "nid:" not in md.read_text(encoding="utf-8")

    await execute_sync(make_config(single_card_vault))

    text = md.read_text(encoding="utf-8")
    assert "nid:" in text
    col = open_collection(anki_base)
    try:
        nid = col.find_notes("")[0]
    finally:
        col.close()
    assert str(nid) in text, "the id in the note must be the id Anki assigned"


@pytest.mark.asyncio
async def test_a_second_sync_creates_nothing(single_card_vault: Path, anki_base: Path, make_config):
    """Two runs over an unchanged vault leave one note, not two.

    This is the duplicate storm in miniature: 6814 twins came from runs that
    could not recognize what they had already written.
    """
    await execute_sync(make_config(single_card_vault))
    await execute_sync(make_config(single_card_vault))

    col = open_collection(anki_base)
    try:
        assert len(col.find_notes("")) == 1
    finally:
        col.close()


@pytest.mark.asyncio
async def test_dry_run_writes_to_neither_anki_nor_the_vault(
    single_card_vault: Path, anki_base: Path, make_config
):
    """A dry run reports what it would do and changes nothing.

    Two dry runs created 555 notes each before this was fixed.
    """
    md = single_card_vault / "Transformer.md"
    before = md.read_text(encoding="utf-8")

    stats = await execute_sync(make_config(single_card_vault, dry_run=True))

    assert stats.total_generated == 1, "it still reports what it would have done"
    assert md.read_text(encoding="utf-8") == before, "the vault is untouched"
    col = open_collection(anki_base)
    try:
        assert col.find_notes("") == [], "Anki is untouched"
    finally:
        col.close()


@pytest.mark.asyncio
async def test_every_note_reaches_its_own_deck(vault: Path, anki_base: Path, make_config):
    """Three cards across two decks, each filed where its own note says."""
    stats = await execute_sync(make_config(vault))
    assert (stats.total_generated, stats.total_imported, stats.total_errors) == (3, 3, 0)

    col = open_collection(anki_base)
    try:
        by_deck = {}
        for nid in col.find_notes(""):
            by_deck.setdefault(deck_of(col, nid), []).append(nid)
        assert sorted(by_deck) == ["AI::Deep Learning::Transformers", "Statistics::Inference"]
        assert len(by_deck["AI::Deep Learning::Transformers"]) == 1
        assert len(by_deck["Statistics::Inference"]) == 2
    finally:
        col.close()


@pytest.mark.xfail(
    strict=True,
    reason="Known gap: reconcile-by-arete-tag is implemented in the AnkiConnect adapter "
    "only. The direct backend creates a second copy when a note id goes stale. "
    "Delete this marker when the direct backend reconciles too.",
)
@pytest.mark.asyncio
async def test_a_stale_note_id_reconciles_instead_of_duplicating(
    single_card_vault: Path, anki_base: Path, make_config
):
    """A vault whose id no longer matches Anki must heal, not make a second copy.

    This is how the 6814 duplicates were born: a restored or re-synced vault carried
    ids Anki had never issued, and every run created the card again.
    """
    md = single_card_vault / "Transformer.md"
    await execute_sync(make_config(single_card_vault))

    col = open_collection(anki_base)
    try:
        real_nid = col.find_notes("")[0]
    finally:
        col.close()

    # The note is still in Anki. The vault now points at an id that never existed.
    md.write_text(
        md.read_text(encoding="utf-8").replace(str(real_nid), "1111111111111"), encoding="utf-8"
    )

    await execute_sync(make_config(single_card_vault, force=True))

    col = open_collection(anki_base)
    try:
        nids = col.find_notes("")
        assert len(nids) == 1, f"expected the existing card to be healed, found {len(nids)} copies"
    finally:
        col.close()
