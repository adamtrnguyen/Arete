"""Metrics calculator for deriving insights from raw FSRS stats.

This is a pure computation module with no I/O.
"""

import time
from dataclasses import dataclass, field

from arete.domain.stats.models import CardStatsAggregate, ReviewEntry

DEFAULT_FSRS6_DECAY = 0.1542
FSRS5_DECAY = 0.5
DEFAULT_RETENTION = 0.9


def _resolve_decay(card: CardStatsAggregate, deck_params: dict | None = None) -> float:
    """Resolve the FSRS forgetting-curve decay exponent `d` for a card.

    Precedence:
    1. Per-card `card.fsrs.decay` (Anki 26 / FSRS-6), if positive.
    2. Deck FSRS-6 parameter `weights[20]`, if `len(weights) >= 21` and positive.
    3. FSRS-4.5 / FSRS-5 fixed decay `0.5`, if `len(weights) in (17, 19)`.
    4. FSRS-6 default decay `0.1542`.
    """
    if card.fsrs and card.fsrs.decay is not None and card.fsrs.decay > 0:
        return float(card.fsrs.decay)

    weights = (deck_params or {}).get("weights") or []
    if (
        len(weights) >= 21
        and isinstance(weights[20], (int, float))
        and not isinstance(weights[20], bool)
        and weights[20] > 0
    ):
        return float(weights[20])
    if len(weights) in (17, 19):
        return FSRS5_DECAY
    return DEFAULT_FSRS6_DECAY


def _resolve_desired_retention(card: CardStatsAggregate, deck_params: dict | None = None) -> float:
    """Resolve target retention in (0, 1) from card state, deck config, or 0.9 default."""
    if (
        card.fsrs
        and card.fsrs.desired_retention is not None
        and 0.0 < card.fsrs.desired_retention < 1.0
    ):
        return float(card.fsrs.desired_retention)

    deck_dr = (deck_params or {}).get("desired_retention")
    if isinstance(deck_dr, (int, float)) and not isinstance(deck_dr, bool) and 0.0 < deck_dr < 1.0:
        return float(deck_dr)
    return DEFAULT_RETENTION


def _forgetting_curve(days_elapsed: float, stability: float, decay: float) -> float:
    """FSRS-6 power-law retrievability: `R(t, S) = (1 + factor * t / S)^(-d)`."""
    t = max(0.0, days_elapsed)
    factor = 0.9 ** (-1.0 / decay) - 1.0
    return (1.0 + factor * t / stability) ** (-decay)


def _predicted_interval(stability: float, desired_retention: float, decay: float) -> float:
    """FSRS-6 inverse forgetting curve: interval (days) where `R(t, S) == desired_retention`."""
    factor = 0.9 ** (-1.0 / decay) - 1.0
    return (stability / factor) * (desired_retention ** (-1.0 / decay) - 1.0)


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
    interval_growth: float | None
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
        d_params = deck_params or {}

        retrievability = self._compute_retrievability(card, d_params)
        lapse_rate = self._compute_lapse_rate(card)
        volatility = self._compute_volatility(card.reviews)
        days_overdue = self._compute_days_overdue(card)
        adherence = self._compute_schedule_adherence(card, d_params)

        # Detect missing history:
        # If we have reviews but NONE of them have FSRS stability data,
        # then the backend history is likely missing the 'data' column.
        reviews = card.reviews or []
        has_history_data = any(r.stability is not None for r in reviews)
        fsrs_history_missing = len(reviews) > 0 and not has_history_data

        # Use aggregate average time if available
        avg_time = card.average_time_ms
        desired_retention = (
            card.fsrs.desired_retention
            if card.fsrs and card.fsrs.desired_retention is not None
            else d_params.get("desired_retention")
        )

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
            difficulty=(
                card.fsrs.difficulty if card.fsrs and card.fsrs.difficulty is not None else None
            ),
            current_retrievability=retrievability,
            lapse_rate=lapse_rate,
            volatility=volatility,
            days_overdue=days_overdue,
            interval_growth=self._compute_interval_growth(card.reviews),
            press_fatigue=self._compute_press_fatigue(card.reviews),
            average_time_ms=avg_time,
            ret_at_review=self._compute_ret_at_review(card.reviews),
            schedule_adherence=adherence,
            is_overlearning=self._detect_overlearning(card, d_params),
            answer_distribution=card.answer_distribution,
            desired_retention=desired_retention,
            weights=d_params.get("weights", []),
            fsrs_history_missing=fsrs_history_missing,
        )

    def _compute_retrievability(
        self, card: CardStatsAggregate, deck_params: dict | None = None
    ) -> float | None:
        """Compute current recall probability using the FSRS-6 power-law forgetting curve.

        `factor = 0.9^(-1/d) - 1`, `R(t, S) = (1 + factor * t / S)^(-d)`.
        """
        if not card.fsrs or not card.last_review:
            return None

        if card.fsrs.stability <= 0:
            return None

        now_epoch = int(time.time())
        days_elapsed = (now_epoch - card.last_review) / 86400.0
        decay = _resolve_decay(card, deck_params)
        return _forgetting_curve(days_elapsed, card.fsrs.stability, decay)

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

        # Stability values when the reviews carry them.
        stabilities = [r.stability for r in recent if r.stability is not None]

        if len(stabilities) >= 2:
            mean = sum(stabilities) / len(stabilities)
            variance = sum((s - mean) ** 2 for s in stabilities) / len(stabilities)
            return variance

        # Without stability history: the raw variance of the intervals. It is not
        # normalized, so it is on a different scale from the stability variance.
        intervals = [r.interval for r in recent]
        if len(intervals) < 2:
            return None

        mean = sum(intervals) / len(intervals)
        if mean == 0:
            return 0.0
        variance = sum((i - mean) ** 2 for i in intervals) / len(intervals)
        return variance

    def _compute_schedule_adherence(
        self, card: CardStatsAggregate, deck_params: dict | None = None
    ) -> float | None:
        """Ratio of Actual Interval / Predicted Interval for the target desired retention.

        Uses the FSRS-6 inverse forgetting curve:
        `predicted_ivl = (S / factor) * (R_d^(-1/d) - 1)`.
        At `R_d == 0.9`, `predicted_ivl == S`.
        1.0 means perfect adherence to FSRS scheduling.
        < 1.0 means early review. > 1.0 means late review.
        """
        if not card.fsrs or card.fsrs.stability <= 0 or card.interval == 0:
            return None

        decay = _resolve_decay(card, deck_params)
        desired_retention = _resolve_desired_retention(card, deck_params)
        predicted = _predicted_interval(card.fsrs.stability, desired_retention, decay)
        if predicted <= 0:
            return None
        return card.interval / predicted

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

        # Growth is only meaningful between review intervals. From a learning step
        # (10 min -> 2 d) the ratio is 288x and says nothing about spacing, so a previous
        # interval under a day -- new, learning or relearning -- gives no growth.
        if latest.last_interval < 1:
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

    def _detect_overlearning(
        self, card: CardStatsAggregate, deck_params: dict | None = None
    ) -> bool:
        """Flag mature cards (`stability >= 90`) that were reviewed far earlier than due.

        Checks the latest review's recorded retrievability (`> 0.98`), or — when review
        timestamps exist without FSRS revlog data — whether the gap between the last two
        reviews was `< 10%` of stability (producing `R > 0.98`). Never uses `now - last_review`,
        which would falsely flag every normal mature card right after an on-schedule review.
        """
        if not card.fsrs or card.fsrs.stability < 90:
            return False

        reviews = card.reviews or []
        if not reviews:
            return False

        latest = reviews[-1]
        if latest.retrievability is not None:
            return latest.retrievability > 0.98

        if len(reviews) < 2:
            return False

        prev = reviews[-2]
        gap_days = (latest.review_time - prev.review_time) / 86400.0
        if gap_days < 0:
            return False

        decay = _resolve_decay(card, deck_params)
        retrievability = _forgetting_curve(gap_days, card.fsrs.stability, decay)
        return retrievability > 0.98 and gap_days < (card.fsrs.stability * 0.1)
