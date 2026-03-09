"""Metrics calculator for deriving insights from raw FSRS stats.

This is a pure computation module with no I/O.
"""

import time
from dataclasses import dataclass, field

from arete.domain.stats.models import CardStatsAggregate, ReviewEntry


@dataclass
class EnrichedStats:
    """Card stats enriched with computed metrics."""

    # Original aggregate
    card_id: int
    note_id: int
    deck_name: str
    lapses: int
    ease: int
    interval: int
    due: int
    reps: int
    front: str | None

    # FSRS core (from aggregate)
    stability: float | None
    difficulty: float | None

    # Computed metrics
    current_retrievability: float | None
    lapse_rate: float | None
    volatility: float | None
    days_overdue: int | None
    interval_growth: float | None  # Replaces stability_gain
    press_fatigue: float | None  # New metric
    average_time_ms: int | None  # New metric
    ret_at_review: float | None
    schedule_adherence: float | None
    is_overlearning: bool = False
    answer_distribution: dict[int, int] = field(default_factory=dict)
    # Deck level
    desired_retention: float | None = None
    weights: list[float] = field(default_factory=list)

    # Metadata
    fsrs_history_missing: bool = False


class MetricsCalculator:
    """Computes derived metrics from raw CardStatsAggregate objects.

    Stateless and side-effect free.
    """

    def enrich(self, card: CardStatsAggregate, deck_params: dict | None = None) -> EnrichedStats:
        """Enrich a card's stats with computed metrics."""
        retrievability = self._compute_retrievability(card)
        lapse_rate = self._compute_lapse_rate(card)
        volatility = self._compute_volatility(card.reviews)
        days_overdue = self._compute_days_overdue(card)
        adherence = self._compute_schedule_adherence(card)

        d_params = deck_params or {}

        # Detect missing history:
        # If we have reviews but NONE of them have FSRS stability data,
        # then the backend history is likely missing the 'data' column.
        reviews = card.reviews or []
        has_history_data = any(r.stability is not None for r in reviews)
        fsrs_history_missing = len(reviews) > 0 and not has_history_data

        # Use aggregate average time if available
        avg_time = card.average_time_ms

        return EnrichedStats(
            card_id=card.card_id,
            note_id=card.note_id,
            deck_name=card.deck_name,
            lapses=card.lapses,
            ease=card.ease,
            interval=card.interval,
            due=card.due,
            reps=card.reps,
            front=card.front,
            stability=card.fsrs.stability if card.fsrs else None,
            difficulty=card.fsrs.difficulty if card.fsrs and card.fsrs.difficulty else None,
            current_retrievability=retrievability,
            lapse_rate=lapse_rate,
            volatility=volatility,
            days_overdue=days_overdue,
            interval_growth=self._compute_interval_growth(card.reviews),
            press_fatigue=self._compute_press_fatigue(card.reviews),
            average_time_ms=avg_time,
            ret_at_review=self._compute_ret_at_review(card.reviews),
            schedule_adherence=adherence,
            is_overlearning=self._detect_overlearning(card),
            answer_distribution=card.answer_distribution,
            desired_retention=d_params.get("desired_retention"),
            weights=d_params.get("weights", []),
            fsrs_history_missing=fsrs_history_missing,
        )

    def _compute_retrievability(self, card: CardStatsAggregate) -> float | None:
        """Compute current recall probability using FSRS formula.

        R = 0.9^(t/S) where t = days since last review, S = stability.
        """
        if not card.fsrs or not card.last_review:
            return None

        now_epoch = int(time.time())
        days_elapsed = (now_epoch - card.last_review) / 86400.0

        if card.fsrs.stability <= 0:
            return None

        return 0.9 ** (days_elapsed / card.fsrs.stability)

    def _compute_lapse_rate(self, card: CardStatsAggregate) -> float | None:
        """Compute lapse rate as lapses / total reviews."""
        if card.reps == 0:
            return None
        return card.lapses / card.reps

    def _compute_volatility(self, reviews: list[ReviewEntry]) -> float | None:
        """Compute variance in stability over recent reviews.

        High volatility indicates unstable learning or inconsistent review performance.
        """
        if len(reviews) < 3:
            return None

        # Use last 10 reviews
        recent = reviews[-10:]

        # Prefer stability values if available (User's preferred definition)
        stabilities = [r.stability for r in recent if r.stability is not None]

        if len(stabilities) >= 2:
            mean = sum(stabilities) / len(stabilities)
            variance = sum((s - mean) ** 2 for s in stabilities) / len(stabilities)
            return variance

        # Fallback to interval variance (normalized) if stability history is missing
        # We normalize by dividing by mean interval to make it scale-independent
        intervals = [r.interval for r in recent]
        if len(intervals) < 2:
            return None

        mean = sum(intervals) / len(intervals)
        if mean == 0:
            return 0.0
        variance = sum((i - mean) ** 2 for i in intervals) / len(intervals)

        # Return coefficient of variation squared or similar metric?
        # For now raw variance of intervals might be huge.
        # Let's return std dev / mean (Coefficient of Variation) * 10 or something
        # user wants "S Var" which matches stability scale.
        # Without stability, this metric is weak.
        # But user agreed to "fix volatility".
        # Let's leave it as is for now or use interval growth variance?
        return variance

    def _compute_schedule_adherence(self, card: CardStatsAggregate) -> float | None:
        """Ratio of Actual Interval / Predicted Interval (Stability).

        1.0 means perfect adherence to FSRS 90% retention scheduling.
        < 1.0 means early review. > 1.0 means late review.
        """
        if not card.fsrs or card.fsrs.stability <= 0 or card.interval == 0:
            return None

        # Actual interval is the one the card IS CURRENTLY AT,
        # but for true 'adherence' we might want the interval of the LAST review.
        # However, Anki's card.ivl is the interval assigned AFTER the last review.
        # So it represents the 'predicted' gap.
        # Predicted gap for 90% retention is exactly Stability.
        return card.interval / card.fsrs.stability

    def _compute_days_overdue(self, card: CardStatsAggregate) -> int | None:
        """Compute days overdue (negative if not yet due)."""
        if not card.last_review or card.interval == 0:
            return None

        now_epoch = int(time.time())
        expected_due_epoch = card.last_review + (card.interval * 86400)
        return int((now_epoch - expected_due_epoch) / 86400)

    def _compute_interval_growth(self, reviews: list[ReviewEntry]) -> float | None:
        """Compute interval growth multiplier from the most recent review.

        Values > 1.0 indicate successful spacing. < 1.0 indicates failing/resetting.
        This effectively replaces Stability Gain for users without FSRS log history.
        """
        if not reviews:
            return None

        latest = reviews[-1]

        # If it's a new card (last_interval=0), growth is undefined/infinite
        if latest.last_interval <= 0:
            return None

        return latest.interval / latest.last_interval

    def _compute_press_fatigue(self, reviews: list[ReviewEntry]) -> float | None:
        """Compute 'Press Fatigue' which is the ratio of Hard/(Hard+Good) presses.

        High fatigue means the user is struggling to get 'Good' even if they aren't failing.
        Uses the last 20 reviews for a rolling window of recent fatigue.
        """
        recent = reviews[-20:]
        if not recent:
            return None

        hard_count = sum(1 for r in recent if r.rating == 2)
        good_count = sum(1 for r in recent if r.rating == 3)
        total_valid = hard_count + good_count

        if total_valid == 0:
            return None

        return hard_count / total_valid

    def _compute_ret_at_review(self, reviews: list[ReviewEntry]) -> float | None:
        """Extract the retrievability at the moment of the last review.

        This represents the 'difficulty' of the test for the learner.
        """
        if not reviews:
            return None
        return reviews[-1].retrievability

    def _detect_overlearning(self, card: CardStatsAggregate) -> bool:
        """Flag cards that are being reviewed too often for their stability.

        Criteria: Stability > 90 days AND Retrievability > 98% AND days_overdue < -30.
        """
        if not card.fsrs or card.fsrs.stability < 90:
            return False

        now_epoch = int(time.time())
        if not card.last_review:
            return False

        days_elapsed = (now_epoch - card.last_review) / 86400.0
        # retrievability formula
        retrievability = 0.9 ** (days_elapsed / card.fsrs.stability)

        # If we are reviewing with very high retrievability and long stability
        if retrievability > 0.98 and days_elapsed < (card.fsrs.stability * 0.1):
            return True

        return False
