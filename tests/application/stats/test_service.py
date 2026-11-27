"""Tests for FsrsStatsService backfilling review aggregates across backends."""

import time

import pytest

from arete.application.stats.metrics_calculator import MetricsCalculator
from arete.application.stats.service import FsrsStatsService
from arete.domain.stats.models import CardStatsAggregate, FsrsMemoryState, ReviewEntry
from arete.domain.stats.ports import StatsRepository


class _FakeStatsRepo(StatsRepository):
    def __init__(
        self,
        stats: list[CardStatsAggregate],
        reviews: list[ReviewEntry],
        deck_params: dict[str, dict],
    ):
        self._stats = stats
        self._reviews = reviews
        self._deck_params = deck_params

    async def get_card_stats(self, nids: list[int]) -> list[CardStatsAggregate]:
        return list(self._stats)

    async def get_review_history(self, cids: list[int]) -> list[ReviewEntry]:
        return list(self._reviews)

    async def get_deck_params(self, deck_names: list[str]) -> dict[str, dict]:
        return dict(self._deck_params)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_backfills_last_review_answer_dist_and_avg_time_from_reviews():
    """AnkiConnect cardsInfo lacks revlog aggregates; service derives them from reviews."""
    now = int(time.time())
    agg = CardStatsAggregate(
        card_id=10,
        note_id=1,
        deck_name="Math",
        lapses=1,
        ease=2500,
        interval=10,
        due=0,
        reps=3,
        fsrs=FsrsMemoryState(stability=20.0, difficulty=0.4),
        last_review=None,
        average_time_ms=0,
        answer_distribution={},
    )
    reviews = [
        ReviewEntry(
            card_id=10,
            review_time=now - 10 * 86400,
            rating=1,
            interval=1,
            last_interval=0,
            time_ms=3000,
        ),
        ReviewEntry(
            card_id=10,
            review_time=now - 5 * 86400,
            rating=3,
            interval=5,
            last_interval=1,
            time_ms=5000,
        ),
        ReviewEntry(
            card_id=10,
            review_time=now - 2 * 86400,
            rating=3,
            interval=10,
            last_interval=5,
            time_ms=4000,
        ),
    ]
    repo = _FakeStatsRepo([agg], reviews, {"Math": {"desired_retention": 0.9, "weights": []}})
    service = FsrsStatsService(repo=repo, calculator=MetricsCalculator())

    [enriched] = await service.get_enriched_stats([1])

    assert enriched.answer_distribution == {1: 1, 3: 2}
    assert enriched.average_time_ms == 4000
    assert enriched.current_retrievability is not None
    assert enriched.days_overdue is not None
