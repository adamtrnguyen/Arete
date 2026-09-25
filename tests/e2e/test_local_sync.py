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


@pytest.mark.asyncio
async def test_changing_a_card_to_cloze_converts_the_note_and_keeps_its_reviews(
    single_card_vault: Path, anki_base: Path, make_config
):
    """A card whose model changes is converted in place, not dropped or duplicated.

    Updating a Basic note with Cloze fields used to match no field names, so the
    card silently stopped updating (21 such cards in a real vault).
    """
    from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter

    await execute_sync(make_config(single_card_vault))
    col = open_collection(anki_base)
    try:
        [nid] = col.find_notes("")
        card = col.get_note(nid).cards()[0]
        card.reps, card.ivl, card.type, card.queue = 7, 30, 2, 2
        col.update_card(card)
        cid = card.id
    finally:
        col.close()

    md = single_card_vault / "Transformer.md"
    meta, body = parse_frontmatter(md.read_text(encoding="utf-8"))
    c = meta["cards"][0]
    c["model"] = "Cloze"
    c["Text"] = "A Transformer block has {{c1::self-attention}} and an MLP."
    c["Back Extra"] = c.pop("Back")
    del c["Front"]
    md.write_text(rebuild_markdown_with_frontmatter(meta, body), encoding="utf-8")

    stats = await execute_sync(make_config(single_card_vault))
    assert stats.total_errors == 0

    col = open_collection(anki_base)
    try:
        assert col.find_notes("") == [nid], "converted in place, not re-created"
        note = col.get_note(nid)
        assert note.note_type()["name"] == "Cloze"
        assert "{{c1::self-attention}}" in note["Text"]
        assert "residual" in note["Back Extra"]
        card = note.cards()[0]
        assert (card.id, card.reps, card.ivl) == (cid, 7, 30), "review history kept"
    finally:
        col.close()


@pytest.mark.asyncio
async def test_an_edit_survives_a_dry_run(single_card_vault: Path, anki_base: Path, make_config):
    """A dry run recorded the edited file as synced, so the real sync skipped the edit."""
    await execute_sync(make_config(single_card_vault))
    md = single_card_vault / "Transformer.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace("each in a residual", "EDITED ANSWER"),
        encoding="utf-8",
    )

    await execute_sync(make_config(single_card_vault, dry_run=True))
    await execute_sync(make_config(single_card_vault, dry_run=True))
    stats = await execute_sync(make_config(single_card_vault))

    assert stats.total_imported == 1
    col = open_collection(anki_base)
    try:
        assert "EDITED ANSWER" in col.get_note(col.find_notes("")[0])["Back"]
    finally:
        col.close()


@pytest.mark.asyncio
async def test_a_warm_run_sends_nothing(single_card_vault: Path, anki_base: Path, make_config):
    """After a clean sync (id write-back included) an unchanged vault sends no card."""
    await execute_sync(make_config(single_card_vault))
    stats = await execute_sync(make_config(single_card_vault))

    assert stats.total_imported == 0


def _add_loose_note(col, deck: str, front: str) -> int:
    """A note made by hand in Anki: no vault card claims it."""
    note = col.new_note(col.models.by_name("Basic"))
    note["Front"], note["Back"] = front, "x"
    col.add_note(note, col.decks.id(deck))
    return note.id


@pytest.mark.asyncio
async def test_prune_keeps_a_claimed_note_moved_to_a_stray_deck(
    single_card_vault: Path, anki_base: Path, make_config, monkeypatch
):
    """Its deck looked orphaned, and deleting the deck deleted the vault's note.

    Not --force: that re-sends every card and moves this one home before prune looks.
    """
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")
    await execute_sync(make_config(single_card_vault))
    col = open_collection(anki_base)
    try:
        [nid] = col.find_notes("")
        col.set_deck([c.id for c in col.get_note(nid).cards()], col.decks.id("Stray"))
    finally:
        col.close()

    await execute_sync(make_config(single_card_vault, prune=True))

    col = open_collection(anki_base)
    try:
        assert col.find_notes("") == [nid], "the vault's note must survive prune"
    finally:
        col.close()


@pytest.mark.asyncio
async def test_prune_deletes_each_orphan_once_and_spares_default(
    single_card_vault: Path, anki_base: Path, make_config
):
    await execute_sync(make_config(single_card_vault))
    col = open_collection(anki_base)
    try:
        [claimed] = col.find_notes("")
        orphans = {
            _add_loose_note(col, "AI::Deep Learning::Transformers", "loose one"),
            _add_loose_note(col, "AI", "loose two"),
        }
        kept = {
            _add_loose_note(col, "Default", "in default"),
            _add_loose_note(col, "Default::Sub", "in a default subdeck"),
        }
    finally:
        col.close()

    await execute_sync(make_config(single_card_vault, prune=True, force=True))

    col = open_collection(anki_base)
    try:
        left = set(col.find_notes(""))
        assert claimed in left
        assert kept <= left, "Default and its subdecks are never pruned"
        assert not (orphans & left)
    finally:
        col.close()
