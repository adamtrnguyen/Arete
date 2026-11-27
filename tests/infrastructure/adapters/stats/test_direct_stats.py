"""Tests for DirectStatsRepository against real tmp_path Anki collections.

Exercises card stats, FSRS-6 memory state, revlog aggregates, review history
(including ``time_ms`` and chunked merging), deck FSRS params, and filtered-deck
home-deck resolution against real Anki 26 SQLite collections. Minimal typed fakes
are used only for unopenable-collection and mid-query DB error propagation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from anki.collection import Collection

from arete.application.stats.metrics_calculator import MetricsCalculator
from arete.application.stats.service import FsrsStatsService
from arete.infrastructure.adapters.stats import DirectStatsRepository
from arete.infrastructure.adapters.stats.direct_stats import _SQL_CHUNK
from tests.e2e.conftest import PROFILE, make_anki_base

# ---------------------------------------------------------------------------
# Helpers & minimal typed fakes
# ---------------------------------------------------------------------------


@contextmanager
def _open_col(base: Path) -> Iterator[Collection]:
    col = Collection(str(base / PROFILE / "collection.anki2"))
    try:
        yield col
    finally:
        col.close()


def _add_note(
    col: Collection,
    *,
    deck: str = "Default",
    front: str = "Q",
    back: str = "A",
    model: str = "Basic",
) -> tuple[int, list[int]]:
    did = col.decks.id(deck)
    assert did is not None
    notetype = col.models.by_name(model)
    assert notetype is not None
    note = col.new_note(notetype)
    if model == "Cloze":
        note["Text"] = front
    else:
        note["Front"] = front
        note["Back"] = back
    col.add_note(note, did)
    return int(note.id), [int(c) for c in note.card_ids()]


class _NullCollectionRepo:
    """Typed fake simulating an AnkiRepository whose collection did not open."""

    def __init__(self, _base: Path | None = None):
        """Initialize with `col = None`."""
        self.col = None

    def __enter__(self) -> _NullCollectionRepo:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class _FailingRevlogRepo:
    """Typed fake whose cards query succeeds and whose revlog query raises."""

    def __init__(self, _base: Path | None = None):
        """Initialize with a db stub that fails on the revlog query."""

        class _Db:
            def all(self, query: str, *_args):
                if query.startswith("SELECT nid, id FROM cards"):
                    return [(3, 301)]
                raise RuntimeError("locked")

        self.col = SimpleNamespace(db=_Db())

    def __enter__(self) -> _FailingRevlogRepo:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class _FailingGetCardRepo:
    """Typed fake whose `get_card` raises for the first card."""

    def __init__(self, _base: Path | None = None):
        """Initialize with a collection whose `get_card` raises."""

        class _Db:
            def all(self, query: str, *_args):
                if query.startswith("SELECT nid, id FROM cards"):
                    return [(1, 101), (2, 201)]
                return []

        def _get_card(_cid):
            raise RuntimeError("bad card")

        self.col = SimpleNamespace(db=_Db(), get_card=_get_card)

    def __enter__(self) -> _FailingGetCardRepo:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


@pytest.fixture
def anki_base(tmp_path: Path) -> Path:
    return make_anki_base(tmp_path / "anki")


# ---------------------------------------------------------------------------
# get_card_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_card_stats_empty_nids(anki_base: Path):
    """Empty NID list returns empty list immediately."""
    repo = DirectStatsRepository(anki_base=anki_base)
    assert await repo.get_card_stats([]) == []


@pytest.mark.asyncio
async def test_get_card_stats_no_collection():
    """A collection that did not open raises: no stats would read as no cards."""
    with patch(
        "arete.infrastructure.adapters.stats.direct_stats.AnkiRepository",
        _NullCollectionRepo,
    ):
        with pytest.raises(OSError, match="did not open"):
            await DirectStatsRepository(anki_base=None).get_card_stats([1])


@pytest.mark.asyncio
async def test_get_card_stats_basic(anki_base: Path):
    """Basic stats fetching from a real Anki collection."""
    with _open_col(anki_base) as col:
        nid, [cid] = _add_note(
            col, deck="Science", front="What is DNA?", back="Deoxyribonucleic acid"
        )
        card = col.get_card(cid)
        card.lapses = 3
        card.factor = 2500
        card.ivl = 14
        card.due = 42
        card.reps = 20
        col.update_card(card)

    result = await DirectStatsRepository(anki_base=anki_base).get_card_stats([nid])

    assert len(result) == 1
    s = result[0]
    assert s.card_id == cid
    assert s.note_id == nid
    assert s.lapses == 3
    assert s.ease == 2500
    assert s.deck_name == "Science"
    assert s.interval == 14
    assert s.due == 42
    assert s.reps == 20
    assert s.front == "What is DNA?"
    assert s.fsrs is None
    assert (s.last_review, s.average_time_ms, s.answer_distribution) == (None, 0, {})


@pytest.mark.asyncio
async def test_get_card_stats_fsrs6_memory_state_and_last_review_fallback(anki_base: Path):
    """Real Anki 26 Card memory_state, decay, desired_retention, and last_review_time fallback."""
    with _open_col(anki_base) as col:
        from anki.cards import FSRSMemoryState  # after anki.collection to avoid import cycle

        nid, [cid] = _add_note(col, front="Q")
        card = col.get_card(cid)
        card.memory_state = FSRSMemoryState(stability=30.0, difficulty=7.5)
        card.decay = 0.22
        card.desired_retention = 0.85
        card.last_review_time = 1_700_005_678
        col.update_card(card)

    [s] = await DirectStatsRepository(anki_base=anki_base).get_card_stats([nid])

    assert s.fsrs is not None
    assert s.fsrs.difficulty == pytest.approx(0.75)  # normalised 0-1, matching AnkiConnect
    assert s.fsrs.stability == pytest.approx(30.0)
    assert s.fsrs.retrievability is None  # computed by application layer
    assert s.fsrs.decay == pytest.approx(0.22)
    assert s.fsrs.desired_retention == pytest.approx(0.85)
    assert s.last_review == 1_700_005_678


@pytest.mark.asyncio
async def test_get_card_stats_deck_missing_and_orphaned_note(anki_base: Path):
    """Deleted deck falls back via Anki's decks.get without crashing; deleted note gives front=None."""
    with _open_col(anki_base) as col:
        gone_did = col.decks.id("Gone")
        assert gone_did is not None
        nid_gone_deck, [cid_gone_deck] = _add_note(col, front="Has note")
        col.decks.remove([gone_did])
        col.db.execute("UPDATE cards SET did = ? WHERE id = ?", gone_did, cid_gone_deck)

        nid_orphan, _ = _add_note(col, front="Will be deleted")
        col.db.execute("DELETE FROM notes WHERE id = ?", nid_orphan)

    stats = await DirectStatsRepository(anki_base=anki_base).get_card_stats(
        [nid_gone_deck, nid_orphan]
    )
    by_nid = {s.note_id: s for s in stats}
    assert by_nid[nid_gone_deck].deck_name == "Default"
    assert by_nid[nid_gone_deck].front == "Has note"
    assert by_nid[nid_orphan].front is None


@pytest.mark.asyncio
async def test_get_card_stats_revlog_aggregates_and_answer_distribution(anki_base: Path):
    """Last review, truncated mean review time, and answer distribution come from real revlog."""
    with _open_col(anki_base) as col:
        nid, [cid] = _add_note(col, front="Q")
        rows = [
            # rating 1 (Again) x2, rating 3 (Good) x2, rating 4 (Easy) x1
            # total time = 5000 + 2000 + 1000 + 1001 + 1000 = 10001 -> int(10001 / 5) = 2000
            (1_700_000_000_000, cid, -1, 1, 1, 0, 2500, 5000, 1),
            (1_695_000_000_000, cid, -1, 1, 1, 0, 2500, 2000, 1),
            (1_690_000_000_000, cid, -1, 3, 5, 1, 2500, 1000, 1),
            (1_685_000_000_000, cid, -1, 3, 10, 5, 2500, 1001, 1),
            (1_680_000_000_000, cid, -1, 4, 15, 10, 2500, 1000, 1),
        ]
        col.db.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    [s] = await DirectStatsRepository(anki_base=anki_base).get_card_stats([nid])
    assert s.last_review == 1_700_000_000
    assert s.average_time_ms == 2000
    assert s.answer_distribution == {1: 2, 3: 2, 4: 1}


@pytest.mark.asyncio
async def test_get_card_stats_multiple_cards_per_note(anki_base: Path):
    """A Cloze note with multiple cards returns stats for each card."""
    with _open_col(anki_base) as col:
        nid, cids = _add_note(col, model="Cloze", front="{{c1::alpha}} and {{c2::beta}}")
        assert len(cids) == 2
        card_b = col.get_card(cids[1])
        card_b.lapses = 2
        col.update_card(card_b)

    result = await DirectStatsRepository(anki_base=anki_base).get_card_stats([nid])
    assert [s.card_id for s in result] == sorted(cids)
    by_cid = {s.card_id: s for s in result}
    assert by_cid[cids[0]].lapses == 0
    assert by_cid[cids[1]].lapses == 2


@pytest.mark.asyncio
async def test_filtered_deck_queued_card_resolves_home_deck_and_safe_deck_params(anki_base: Path):
    """Real Anki 26 filtered deck: card.odid resolves home deck and filtered deck lacks `conf`."""
    with _open_col(anki_base) as col:
        from anki.cards import FSRSMemoryState  # after anki.collection to avoid import cycle

        math_did = col.decks.id("Math")
        assert math_did is not None
        base_cfg = col.decks.get_config(1)
        assert base_cfg is not None
        cfg_id = col.decks.add_config_returning_id("MathCfg", base_cfg)
        cfg = col.decks.get_config(cfg_id)
        assert cfg is not None
        cfg["desiredRetention"] = 0.85
        cfg["fsrsParams6"] = [0.2] * 20 + [0.35]
        col.decks.update_config(cfg)
        math_deck = col.decks.get(math_did)
        assert math_deck is not None
        col.decks.set_config_id_for_deck_dict(math_deck, cfg_id)

        nid, [cid] = _add_note(col, deck="Math", front="Home Deck Q", back="A")
        card = col.get_card(cid)
        card.memory_state = FSRSMemoryState(stability=10.0, difficulty=5.5)
        card.decay = 0.18
        card.desired_retention = 0.85
        col.update_card(card)

        fdid = col.decks.new_filtered("Arete::Queue")
        fdeck = col.decks.get(fdid)
        assert fdeck is not None
        assert "conf" not in fdeck  # Real Anki 26 filtered decks have no 'conf' key
        fdeck["terms"] = [[f"cid:{cid}", 1, 0]]
        col.decks.save(fdeck)
        col.sched.rebuild_filtered_deck(fdid)

        queued_card = col.get_card(cid)
        assert queued_card.did == fdid
        assert queued_card.odid == math_did

    repo = DirectStatsRepository(anki_base=anki_base)

    # Direct deck-param lookup on a filtered deck degrades safely to defaults (no KeyError).
    fparams = await repo.get_deck_params(["Arete::Queue"])
    assert fparams["Arete::Queue"] == {
        "desired_retention": 0.9,
        "weights": [],
        "sm2_retention": 0.9,
    }

    # Card stats resolve the card's home deck via `card.odid`.
    [stat] = await repo.get_card_stats([nid])
    assert stat.deck_name == "Math"

    # Full FsrsStatsService enrichment uses the home deck's FSRS-6 config.
    service = FsrsStatsService(repo=repo, calculator=MetricsCalculator())
    [enriched] = await service.get_enriched_stats([nid])
    assert enriched.deck_name == "Math"
    assert enriched.desired_retention == pytest.approx(0.85)
    assert len(enriched.weights) == 21
    assert enriched.weights[20] == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_get_card_stats_errors_propagate():
    """Failed card reads or revlog queries raise rather than returning partial stats."""
    with patch(
        "arete.infrastructure.adapters.stats.direct_stats.AnkiRepository",
        _FailingGetCardRepo,
    ):
        with pytest.raises(RuntimeError, match="bad card"):
            await DirectStatsRepository(anki_base=None).get_card_stats([1, 2])

    with patch(
        "arete.infrastructure.adapters.stats.direct_stats.AnkiRepository",
        _FailingRevlogRepo,
    ):
        with pytest.raises(RuntimeError, match="locked"):
            await DirectStatsRepository(anki_base=None).get_card_stats([3])


# ---------------------------------------------------------------------------
# get_review_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_review_history_empty_and_no_collection(anki_base: Path):
    assert await DirectStatsRepository(anki_base=anki_base).get_review_history([]) == []
    with patch(
        "arete.infrastructure.adapters.stats.direct_stats.AnkiRepository",
        _NullCollectionRepo,
    ):
        with pytest.raises(OSError, match="did not open"):
            await DirectStatsRepository(anki_base=None).get_review_history([101])


@pytest.mark.asyncio
async def test_get_review_history_basic_and_time_ms(anki_base: Path):
    """Review history parses timestamps, intervals, ratings, and time_ms from real revlog."""
    with _open_col(anki_base) as col:
        _, [cid] = _add_note(col, front="Q")
        col.db.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1_690_000_000_000, cid, -1, 2, -600, 0, 2500, 1800, 0),
                (1_700_000_000_000, cid, -1, 3, 7, 1, 2500, 4200, 1),
            ],
        )

    result = await DirectStatsRepository(anki_base=anki_base).get_review_history([cid])
    assert len(result) == 2
    assert result[0].card_id == cid
    assert result[0].review_time == 1_690_000_000
    assert result[0].rating == 2
    assert result[0].time_ms == 1800
    assert result[1].review_time == 1_700_000_000
    assert result[1].rating == 3
    assert result[1].interval == 7
    assert result[1].last_interval == 1
    assert result[1].time_ms == 4200


@pytest.mark.asyncio
async def test_get_review_history_with_fsrs_data_column(anki_base: Path):
    """When revlog has a `data` column with FSRS JSON, s/d/r and time_ms are all extracted."""
    fsrs_data = json.dumps({"s": 15.0, "d": 5.0, "r": 0.92})
    with _open_col(anki_base) as col:
        _, [cid] = _add_note(col, front="Q")
        cols = [c[1] for c in col.db.execute("PRAGMA table_info(revlog)")]
        if "data" not in cols:
            col.db.execute("ALTER TABLE revlog ADD COLUMN data TEXT NOT NULL DEFAULT ''")
        col.db.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            1_700_000_000_000,
            cid,
            -1,
            3,
            7,
            1,
            2500,
            3500,
            1,
            fsrs_data,
        )

    [entry] = await DirectStatsRepository(anki_base=anki_base).get_review_history([cid])
    assert entry.stability == 15.0
    assert entry.difficulty == 5.0
    assert entry.retrievability == 0.92
    assert entry.time_ms == 3500


@pytest.mark.asyncio
async def test_get_review_history_chunks_over_sql_chunk_and_sorts(anki_base: Path):
    """Large CID lists are chunked under SQLite's variable limit and merged chronologically."""
    cids = list(range(1, _SQL_CHUNK + 25))
    with _open_col(anki_base) as col:
        col.db.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (2_000_000_000_000, 1, -1, 3, 7, 1, 2500, 2500, 1),
                (1_000_000_000_000, _SQL_CHUNK + 1, -1, 2, 1, 0, 2500, 1500, 1),
            ],
        )

    repo = DirectStatsRepository(anki_base=anki_base)
    result = await repo.get_review_history(cids)
    assert [r.review_time for r in result] == [1_000_000_000, 2_000_000_000]
    assert [r.time_ms for r in result] == [1500, 2500]
    await repo.close()


# ---------------------------------------------------------------------------
# get_deck_params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deck_params_no_collection():
    with patch(
        "arete.infrastructure.adapters.stats.direct_stats.AnkiRepository",
        _NullCollectionRepo,
    ):
        with pytest.raises(OSError, match="did not open"):
            await DirectStatsRepository(anki_base=None).get_deck_params(["MyDeck"])


@pytest.mark.asyncio
async def test_get_deck_params_custom_missing_and_filtered(anki_base: Path):
    """Real deck configs extract FSRS params; missing decks and filtered decks get defaults."""
    with _open_col(anki_base) as col:
        my_did = col.decks.id("MyDeck")
        assert my_did is not None
        base_cfg = col.decks.get_config(1)
        assert base_cfg is not None
        cfg_id = col.decks.add_config_returning_id("CustomCfg", base_cfg)
        cfg = col.decks.get_config(cfg_id)
        assert cfg is not None
        cfg["desiredRetention"] = 0.85
        cfg["sm2Retention"] = 0.88
        cfg["fsrsParams6"] = [0.4, 0.6, 2.4, 5.8] + [0.1] * 17
        col.decks.update_config(cfg)
        my_deck = col.decks.get(my_did)
        assert my_deck is not None
        col.decks.set_config_id_for_deck_dict(my_deck, cfg_id)

        col.decks.new_filtered("Arete::Queue")

    result = await DirectStatsRepository(anki_base=anki_base).get_deck_params(
        ["MyDeck", "NonExistent", "Arete::Queue"]
    )
    assert result["MyDeck"]["desired_retention"] == pytest.approx(0.85)
    assert result["MyDeck"]["sm2_retention"] == pytest.approx(0.88)
    assert result["MyDeck"]["weights"][:4] == [
        pytest.approx(0.4),
        pytest.approx(0.6),
        pytest.approx(2.4),
        pytest.approx(5.8),
    ]
    assert result["NonExistent"] == {"desired_retention": 0.9, "weights": [], "sm2_retention": 0.9}
    assert result["Arete::Queue"] == {"desired_retention": 0.9, "weights": [], "sm2_retention": 0.9}
