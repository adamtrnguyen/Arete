"""DirectStatsRepository.get_card_stats against a real collection in tmp_path.

The grouped queries must give exactly what the per-card reads give. The reference
below is the per-card algorithm (find_cards per note; get_card, decks.get, get_note and
three revlog queries per card), kept here as the oracle.
"""

import asyncio
import logging
from pathlib import Path

import pytest
from anki.collection import Collection

from arete.domain.stats.models import CardStatsAggregate
from arete.infrastructure.adapters.stats import DirectStatsRepository
from arete.infrastructure.adapters.stats.direct_stats import fsrs_state_of
from tests.e2e.conftest import PROFILE, make_anki_base


def _reference(col: Collection, nids: list) -> list[CardStatsAggregate]:
    out = []
    for nid in nids:
        try:
            for cid in col.find_cards(f"nid:{nid}"):
                card = col.get_card(cid)
                deck = col.decks.get(card.did)
                last = col.db.scalar("SELECT MAX(id) FROM revlog WHERE cid = ?", cid)
                avg = col.db.scalar("SELECT AVG(time) FROM revlog WHERE cid = ?", cid)
                dist = dict(
                    col.db.all("SELECT ease, COUNT(*) FROM revlog WHERE cid = ? GROUP BY ease", cid)
                )
                note = col.get_note(card.nid)
                out.append(
                    CardStatsAggregate(
                        card_id=card.id,
                        note_id=card.nid,
                        deck_name=deck["name"] if deck else "Unknown",
                        lapses=card.lapses,
                        ease=card.factor,
                        interval=card.ivl,
                        due=card.due,
                        reps=card.reps,
                        fsrs=fsrs_state_of(card),
                        last_review=last // 1000 if last else None,
                        average_time_ms=int(avg) if avg else 0,
                        reviews=[],
                        front=note.fields[0] if note.fields else None,
                        answer_distribution=dist,
                    )
                )
        except Exception as e:  # the per-card code logged and skipped the note
            logging.debug(f"skipped nid={nid}: {e}")
    return out


def _build(base: Path) -> list:
    col = Collection(str(base / PROFILE / "collection.anki2"))
    try:
        basic = col.models.by_name("Basic")
        cloze = col.models.by_name("Cloze")
        decks = [col.decks.id(name) for name in ("A", "A::B", "C", "Gone")]
        nids = []
        for i in range(12):
            note = col.new_note(basic)
            note["Front"], note["Back"] = f"front {i}", f"back {i}"
            col.add_note(note, decks[i % 3])
            nids.append(note.id)
        note = col.new_note(cloze)
        note["Text"] = "{{c1::a}} {{c2::b}} {{c3::c}}"
        col.add_note(note, decks[1])
        nids.append(note.id)

        cids = [c for nid in nids for c in col.card_ids_of_note(nid)]
        rows, rid = [], 1_700_000_000_000
        for k, cid in enumerate(cids):
            for r in range(k % 5):  # card 0 has no review; times average to non-integers
                rid += 1_000_003
                rows.append((rid, cid, -1, 1 + (k + r) % 4, 3, 1, 2500, 1000 + 333 * r + k, 1))
        col.db.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        card = col.get_card(cids[5])
        card.lapses, card.reps, card.ivl, card.factor = 4, 9, 21, 2300
        col.update_card(card)
        col.decks.remove([decks[3]])  # leaves no card behind; point one card at it below
        col.db.execute("UPDATE cards SET did = ? WHERE id = ?", decks[3], cids[7])
        from anki.cards import FSRSMemoryState  # after anki.collection: import cycle

        card = col.get_card(cids[2])
        card.memory_state = FSRSMemoryState(stability=12.5, difficulty=6.1)
        card.decay = 0.2
        card.desired_retention = 0.85
        col.update_card(card)
        return nids
    finally:
        col.close()


@pytest.mark.parametrize("pick", ["all", "duplicates_and_missing"])
def test_grouped_stats_equal_the_per_card_reads(tmp_path, pick):
    base = make_anki_base(tmp_path / "anki")
    nids = _build(base)
    if pick == "duplicates_and_missing":
        nids = [nids[3], 999_999, nids[-1], nids[3], str(nids[2]), "x", nids[7]]

    got = asyncio.run(DirectStatsRepository(anki_base=base).get_card_stats(nids))

    col = Collection(str(base / PROFILE / "collection.anki2"))
    try:
        want = _reference(col, nids)
    finally:
        col.close()
    assert want, "the oracle found no cards"
    assert any(s.answer_distribution for s in want) and any(s.fsrs for s in want)
    assert any(
        s.fsrs
        and s.fsrs.decay == pytest.approx(0.2)
        and s.fsrs.desired_retention == pytest.approx(0.85)
        for s in got
    )
    assert got == want


def test_queued_card_in_filtered_deck_resolves_home_deck_and_deck_params_do_not_crash(tmp_path):
    """A card sitting in Arete::Queue uses its home deck (odid) and filtered deck params degrade safely."""
    from arete.application.stats.metrics_calculator import MetricsCalculator
    from arete.application.stats.service import FsrsStatsService

    base = make_anki_base(tmp_path / "anki")
    col = Collection(str(base / PROFILE / "collection.anki2"))
    try:
        basic = col.models.by_name("Basic")
        math_did = col.decks.id("Math")
        cfg_id = col.decks.add_config_returning_id("MathCfg", col.decks.get_config(1))
        cfg = col.decks.get_config(cfg_id)
        assert cfg is not None
        cfg["desiredRetention"] = 0.85
        cfg["fsrsParams6"] = [0.2] * 20 + [0.35]
        col.decks.update_config(cfg)
        math_deck = col.decks.get(math_did)
        assert math_deck is not None
        col.decks.set_config_id_for_deck_dict(math_deck, cfg_id)

        note = col.new_note(basic)
        note["Front"], note["Back"] = "Q", "A"
        col.add_note(note, math_did)
        nid = note.id
        (cid,) = note.card_ids()

        fdid = col.decks.new_filtered("Arete::Queue")
        fdeck = col.decks.get(fdid)
        assert fdeck is not None
        fdeck["terms"] = [[f"cid:{cid}", 1, 0]]
        col.decks.save(fdeck)
        col.sched.rebuild_filtered_deck(fdid)
    finally:
        col.close()

    async def _run():
        repo = DirectStatsRepository(anki_base=base)
        # Direct filtered-deck lookup must never KeyError on missing 'conf'.
        fparams = await repo.get_deck_params(["Arete::Queue"])
        assert fparams["Arete::Queue"]["desired_retention"] == 0.9

        service = FsrsStatsService(repo=repo, calculator=MetricsCalculator())
        return await service.get_enriched_stats([nid])

    [enriched] = asyncio.run(_run())
    assert enriched.deck_name == "Math"
    assert enriched.desired_retention == pytest.approx(0.85)
    assert len(enriched.weights) == 21
    assert enriched.weights[20] == pytest.approx(0.35)


def test_get_review_history_populates_time_ms_from_real_revlog(tmp_path):
    base = make_anki_base(tmp_path / "anki")
    nids = _build(base)

    col = Collection(str(base / PROFILE / "collection.anki2"))
    try:
        (cid,) = col.card_ids_of_note(nids[1])
        expected_times = [
            int(t) for (t,) in col.db.all("SELECT time FROM revlog WHERE cid = ? ORDER BY id", cid)
        ]
    finally:
        col.close()

    history = asyncio.run(DirectStatsRepository(anki_base=base).get_review_history([cid]))
    assert expected_times
    assert [entry.time_ms for entry in history] == expected_times
