from arete.application.stats.metrics_calculator import MetricsCalculator
from arete.domain.stats.ports import StatsRepository


class FsrsStatsService:
    """Application service for fetching and enriching card statistics using FSRS logic."""

    def __init__(self, repo: StatsRepository, calculator: MetricsCalculator):
        """Initialize FsrsStatsService."""
        self.repo = repo
        self.calculator = calculator

    async def get_enriched_stats(self, nids: list[int]):
        # 1. Fetch basic stats (one per card corresponding to NIDs)
        stats = await self.repo.get_card_stats(nids)
        if not stats:
            return []

        # 2. Fetch full review history for these cards
        cids = [s.card_id for s in stats]
        all_reviews = await self.repo.get_review_history(cids)

        # Group reviews by card_id
        reviews_by_cid = {}
        for r in all_reviews:
            reviews_by_cid.setdefault(r.card_id, []).append(r)

        # 3. Fetch deck parameters for context
        deck_names = list({s.deck_name for s in stats})
        deck_params = await self.repo.get_deck_params(deck_names)

        # 4. Enrich each card
        results = []
        for s in stats:
            # Attach reviews and backfill aggregates unavailable from cardsInfo (AnkiConnect)
            reviews = reviews_by_cid.get(s.card_id, [])
            s.reviews = reviews
            if reviews:
                if s.last_review is None:
                    s.last_review = max(r.review_time for r in reviews)
                if not s.answer_distribution:
                    dist: dict[int, int] = {}
                    for r in reviews:
                        dist[r.rating] = dist.get(r.rating, 0) + 1
                    s.answer_distribution = dist
                if s.average_time_ms == 0:
                    total_time = sum(r.time_ms for r in reviews)
                    if total_time > 0:
                        s.average_time_ms = int(total_time / len(reviews))

            # Enrich
            d_params = deck_params.get(s.deck_name)
            enriched = self.calculator.enrich(s, d_params)
            results.append(enriched)

        return results

    async def close(self) -> None:
        """Release underlying stats repository resources."""
        await self.repo.close()
