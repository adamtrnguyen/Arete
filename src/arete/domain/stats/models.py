"""Domain models for FSRS statistics.

These are pure data structures with no I/O or external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FsrsMemoryState:
    """FSRS memory state for a card.

    Attributes:
        stability: Days until recall probability drops to 90%.
        difficulty: Card difficulty (0.0-1.0 normalized from Anki's 1-10 scale).
        retrievability: Current probability of recall (0.0-1.0).

    """

    stability: float
    difficulty: float
    retrievability: float | None = None


def revlog_interval_days(ivl: int) -> float:
    """Anki's revlog interval in DAYS.

    Positive values are days; learning and relearning steps are stored as NEGATIVE
    SECONDS. S2 (2026-09-25): they were mixed with days, so a lapse gave an interval
    growth of -20 and a volatility of 88200.
    """
    return ivl if ivl >= 0 else -ivl / 86400


@dataclass(frozen=True)
class ReviewEntry:
    """A single review log entry.

    Attributes:
        card_id: The card that was reviewed.
        review_time: Epoch timestamp of the review.
        rating: Button pressed (1=Again, 2=Hard, 3=Good, 4=Easy).
        interval: Interval assigned after this review (days).
        last_interval: Previous interval (days).
        time_taken: Time taken to answer in milliseconds.
        review_type: 0=learn, 1=review, 2=relearn, 3=early review.
        stability: FSRS stability after this review.
        difficulty: FSRS difficulty after this review.
        retrievability: FSRS retrievability before this review.

    """

    card_id: int
    review_time: int
    rating: int  # 1..4
    interval: float  # new interval, days (see revlog_interval_days)
    last_interval: float  # previous interval, days
    time_taken: int  # ms duration
    review_type: int
    stability: float | None = None
    difficulty: float | None = None
    retrievability: float | None = None


@dataclass
class CardStatsAggregate:
    """Comprehensive statistics for a card, combining Anki data with FSRS metrics.

    This is the rich domain object used for analytics and dependency queue logic.
    """

    card_id: int
    note_id: int
    deck_name: str

    # Core Anki stats
    lapses: int
    ease: int  # SM-2 factor (e.g., 2500 = 250%)
    interval: int  # Current interval in days
    due: int  # Due date as epoch or day number
    reps: int  # Total review count

    # FSRS memory state
    fsrs: FsrsMemoryState | None = None

    # Timing
    last_review: int | None = None  # Epoch of last review
    average_time_ms: int = 0  # From revlog analysis

    # Review history
    reviews: list[ReviewEntry] = field(default_factory=list)

    # Content
    front: str | None = None

    # Extended Metrics
    answer_distribution: dict[int, int] = field(default_factory=dict)
    days_overdue: int | None = None
    interval_growth: float | None = None  # Replaces stability_gain
    press_fatigue: float | None = None  # Hard / (Good + Hard)
    volatility: float | None = None
    lapse_rate: float | None = None


@dataclass
class NoteInsight:
    """Insight for a specific problematic note (e.g. leech)."""

    note_name: str
    issue: str
    lapses: int
    deck: str


@dataclass
class LearningStats:
    """Aggregate learning statistics."""

    total_cards: int
    retention_rate: float | None = None
    problematic_notes: list[NoteInsight] = field(default_factory=list)
