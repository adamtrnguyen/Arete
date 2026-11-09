"""S1/S2 (2026-09-25): stats mixed scales and units."""

from arete.domain.stats.models import revlog_interval_days


def test_relearning_intervals_in_seconds_become_days():
    """S2: revlog stores learning steps as NEGATIVE SECONDS; they were mixed with days."""
    assert revlog_interval_days(30) == 30
    assert revlog_interval_days(-600) == 600 / 86400
    assert revlog_interval_days(0) == 0
