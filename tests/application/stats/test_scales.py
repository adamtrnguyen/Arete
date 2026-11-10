"""Stats keep one scale and unit per quantity."""

from arete.domain.stats.models import revlog_interval_days


def test_relearning_intervals_in_seconds_become_days():
    """Revlog stores learning steps as NEGATIVE SECONDS; they are converted to days."""
    assert revlog_interval_days(30) == 30
    assert revlog_interval_days(-600) == 600 / 86400
    assert revlog_interval_days(0) == 0
