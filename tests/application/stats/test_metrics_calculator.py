"""MetricsCalculator.enrich: the derived numbers the stats dashboard shows."""

import time

import pytest

from arete.application.stats.metrics_calculator import MetricsCalculator
from arete.domain.stats.models import CardStatsAggregate, FsrsMemoryState, ReviewEntry

DAY = 86400


def days_ago(n: float) -> int:
    return int(time.time() - n * DAY)


def review(rating=3, interval=10.0, last_interval=5.0, stability=None, retrievability=None):
    return ReviewEntry(
        card_id=1,
        review_time=0,
        rating=rating,
        interval=interval,
        last_interval=last_interval,
        time_taken=1000,
        review_type=1,
        stability=stability,
        retrievability=retrievability,
    )


def card(*, stability=None, last_review=None, interval=10, reps=5, lapses=1, reviews=()):
    return CardStatsAggregate(
        card_id=1,
        note_id=1,
        deck_name="Default",
        lapses=lapses,
        ease=2500,
        interval=interval,
        due=0,
        reps=reps,
        fsrs=FsrsMemoryState(stability=stability, difficulty=0.5)
        if stability is not None
        else None,
        last_review=last_review,
        reviews=list(reviews),
    )


def enrich(c, params=None):
    return MetricsCalculator().enrich(c, params)


# --- retrievability ---


def test_retrievability_is_90_percent_after_one_stability_period():
    e = enrich(card(stability=10.0, last_review=days_ago(10)))
    assert e.current_retrievability == pytest.approx(0.9, abs=1e-3)


@pytest.mark.parametrize(
    "stability,last_review",
    [(0.0, "recent"), (-3.0, "recent"), (10.0, None), (None, "recent")],
    ids=["zero-stability", "negative-stability", "never-reviewed", "no-fsrs"],
)
def test_retrievability_is_unknown_without_usable_inputs(stability, last_review):
    last = days_ago(1) if last_review == "recent" else None
    assert enrich(card(stability=stability, last_review=last)).current_retrievability is None


# --- lapse rate, adherence, overdue ---


def test_lapse_rate_is_lapses_over_reps_and_none_for_unreviewed_cards():
    assert enrich(card(lapses=2, reps=8)).lapse_rate == 0.25
    assert enrich(card(lapses=0, reps=0)).lapse_rate is None


def test_schedule_adherence_is_interval_over_stability():
    assert enrich(card(stability=20.0, interval=10)).schedule_adherence == 0.5


@pytest.mark.parametrize("stability,interval", [(0.0, 10), (20.0, 0), (None, 10)])
def test_schedule_adherence_is_unknown_without_stability_or_interval(stability, interval):
    assert enrich(card(stability=stability, interval=interval)).schedule_adherence is None


def test_days_overdue_is_negative_before_due_and_positive_after():
    assert enrich(card(last_review=days_ago(10), interval=4)).days_overdue == 6
    # int() truncates toward zero, so a second ticking over between the two clock reads
    # can turn -4 into -3.
    assert enrich(card(last_review=days_ago(1), interval=5)).days_overdue in (-4, -3)


def test_days_overdue_is_unknown_for_unreviewed_or_zero_interval_cards():
    assert enrich(card(last_review=None, interval=4)).days_overdue is None
    assert enrich(card(last_review=days_ago(3), interval=0)).days_overdue is None


# --- volatility ---


def test_volatility_is_population_variance_of_recent_stabilities():
    reviews = [review(stability=s) for s in (1.0, 2.0, 3.0)]
    assert enrich(card(reviews=reviews)).volatility == pytest.approx(2 / 3)


def test_volatility_looks_only_at_the_last_ten_reviews():
    old_outliers = [review(stability=1000.0) for _ in range(5)]
    steady = [review(stability=4.0) for _ in range(10)]
    assert enrich(card(reviews=old_outliers + steady)).volatility == 0.0


def test_volatility_needs_three_reviews():
    reviews = [review(stability=1.0), review(stability=9.0)]
    assert enrich(card(reviews=reviews)).volatility is None


def test_volatility_falls_back_to_interval_variance_without_stability_history():
    reviews = [review(interval=i) for i in (2.0, 4.0, 6.0)]
    assert enrich(card(reviews=reviews)).volatility == pytest.approx(8 / 3)


def test_volatility_falls_back_to_intervals_when_only_one_review_has_stability():
    reviews = [review(interval=2.0, stability=5.0), review(interval=4.0), review(interval=6.0)]
    assert enrich(card(reviews=reviews)).volatility == pytest.approx(8 / 3)


def test_volatility_is_zero_when_every_interval_is_zero():
    reviews = [review(interval=0.0) for _ in range(3)]
    assert enrich(card(reviews=reviews)).volatility == 0.0


# --- press fatigue ---


def test_press_fatigue_is_hard_over_hard_plus_good():
    reviews = [review(rating=2), review(rating=3), review(rating=3), review(rating=3)]
    assert enrich(card(reviews=reviews)).press_fatigue == 0.25


def test_press_fatigue_ignores_again_and_easy():
    reviews = [review(rating=1), review(rating=4), review(rating=2)]
    assert enrich(card(reviews=reviews)).press_fatigue == 1.0


def test_press_fatigue_is_unknown_with_no_hard_or_good_presses():
    assert enrich(card(reviews=[review(rating=1), review(rating=4)])).press_fatigue is None
    assert enrich(card(reviews=[])).press_fatigue is None


def test_press_fatigue_uses_a_window_of_the_last_twenty_reviews():
    old_hard = [review(rating=2) for _ in range(30)]
    recent_good = [review(rating=3) for _ in range(20)]
    assert enrich(card(reviews=old_hard + recent_good)).press_fatigue == 0.0


# --- overlearning ---


def test_overlearning_flags_a_long_stability_card_reviewed_too_soon():
    assert enrich(card(stability=100.0, last_review=days_ago(1))).is_overlearning is True


@pytest.mark.parametrize(
    "stability,last_review",
    [
        (50.0, 1),  # stability under 90 days
        (100.0, 20),  # 20 days elapsed is past 10% of stability
        (100.0, None),  # never reviewed
    ],
)
def test_overlearning_is_not_flagged_otherwise(stability, last_review):
    last = days_ago(last_review) if last_review else None
    assert enrich(card(stability=stability, last_review=last)).is_overlearning is False


def test_overlearning_is_false_without_fsrs():
    assert enrich(card(last_review=days_ago(1))).is_overlearning is False


# --- history flags and deck parameters ---


def test_fsrs_history_missing_only_when_reviews_exist_without_any_stability():
    assert enrich(card(reviews=[review()])).fsrs_history_missing is True
    assert enrich(card(reviews=[review(stability=3.0)])).fsrs_history_missing is False
    assert enrich(card(reviews=[])).fsrs_history_missing is False


def test_ret_at_review_comes_from_the_latest_review():
    reviews = [review(retrievability=0.7), review(retrievability=0.95)]
    assert enrich(card(reviews=reviews)).ret_at_review == 0.95
    assert enrich(card(reviews=[])).ret_at_review is None


def test_deck_parameters_are_carried_through():
    e = enrich(card(), {"desired_retention": 0.9, "weights": [1.0, 2.0]})
    assert e.desired_retention == 0.9
    assert e.weights == [1.0, 2.0]
    bare = enrich(card())
    assert bare.desired_retention is None
    assert bare.weights == []
