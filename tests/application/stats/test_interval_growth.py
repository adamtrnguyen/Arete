"""Interval growth: the dashboard's "Gain" column."""

from arete.application.stats.metrics_calculator import MetricsCalculator
from arete.domain.stats.models import ReviewEntry


def review(interval: float, last_interval: float) -> ReviewEntry:
    return ReviewEntry(
        card_id=1,
        review_time=0,
        rating=3,
        interval=interval,
        last_interval=last_interval,
        time_taken=5000,
        review_type=1,
    )


def growth(interval: float, last_interval: float) -> float | None:
    return MetricsCalculator()._compute_interval_growth([review(interval, last_interval)])


def test_growth_between_review_intervals():
    assert growth(10, 4) == 2.5


def test_no_growth_out_of_a_learning_step():
    """10 min -> 2 d read as "x288"; a learning step is not a spacing interval."""
    assert growth(2, 10 / 1440) is None


def test_no_growth_for_a_new_card():
    assert growth(1, 0) is None
    assert MetricsCalculator()._compute_interval_growth([]) is None
