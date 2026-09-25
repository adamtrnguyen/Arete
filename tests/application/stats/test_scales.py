"""S1/S2 (2026-09-25): stats mixed scales and units."""

from arete.domain.stats.models import revlog_interval_days
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


def test_cardsinfo_difficulty_fallback_uses_the_same_scale_as_getfsrsstats():
    """S1: with no getFSRSStats value, the raw 1-10 difficulty leaked through unscaled."""
    stat = AnkiConnectAdapter._build_card_stat(
        {"cardId": 1, "note": 2, "difficulty": 5.0, "deckName": "D", "fields": {}}, {}
    )
    assert stat.difficulty == 0.5


def test_relearning_intervals_in_seconds_become_days():
    """S2: revlog stores learning steps as NEGATIVE SECONDS; they were mixed with days."""
    assert revlog_interval_days(30) == 30
    assert revlog_interval_days(-600) == 600 / 86400
    assert revlog_interval_days(0) == 0
