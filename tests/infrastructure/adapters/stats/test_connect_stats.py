"""Tests for ConnectStatsRepository — AnkiConnect-based stats fetching."""

from unittest.mock import AsyncMock

import pytest

from arete.domain.stats.models import FsrsMemoryState
from arete.infrastructure.adapters.stats import ConnectStatsRepository


@pytest.fixture
def repo():
    return ConnectStatsRepository(url="http://127.0.0.1:8765")


# ---------- get_card_stats ----------


@pytest.mark.asyncio
async def test_get_card_stats_empty_nids(repo):
    """Empty NID list returns empty list without any HTTP calls."""
    result = await repo.get_card_stats([])
    assert result == []


@pytest.mark.asyncio
async def test_get_card_stats_no_cards_found(repo):
    """When findCards returns no matching cards, result is empty."""
    repo._invoke = AsyncMock(side_effect=[[]])  # findCards returns empty
    result = await repo.get_card_stats([999])
    assert result == []


@pytest.mark.asyncio
async def test_get_card_stats_single_nid(repo):
    """Basic single-NID fetch returns correct CardStatsAggregate."""
    repo._invoke = AsyncMock(
        side_effect=[
            [101],  # findCards
            [  # cardsInfo
                {
                    "cardId": 101,
                    "note": 1,
                    "lapses": 3,
                    "factor": 2500,
                    "deckName": "Science::Biology",
                    "interval": 14,
                    "due": 1700000000,
                    "reps": 25,
                    "fields": {"Front": {"value": "What is mitosis?"}},
                }
            ],
            # getFSRSStats
            [{"cardId": 101, "difficulty": 5.0, "stability": 30.0, "retrievability": 0.92}],
        ]
    )

    result = await repo.get_card_stats([1])

    assert len(result) == 1
    s = result[0]
    assert s.card_id == 101
    assert s.note_id == 1
    assert s.lapses == 3
    assert s.ease == 2500
    assert s.deck_name == "Science::Biology"
    assert s.interval == 14
    assert s.due == 1700000000
    assert s.reps == 25
    assert s.front == "What is mitosis?"

    # FSRS state
    assert s.fsrs is not None
    assert s.fsrs.stability == 30.0
    assert s.fsrs.difficulty == 0.5  # 5.0 / 10.0
    assert s.fsrs.retrievability == 0.92


@pytest.mark.asyncio
async def test_get_card_stats_fsrs_difficulty_normalization(repo):
    """FSRS difficulty is normalized from 0-10 to 0-1 scale (divided by 10)."""
    repo._invoke = AsyncMock(
        side_effect=[
            [201],  # findCards
            [  # cardsInfo
                {
                    "cardId": 201,
                    "note": 2,
                    "lapses": 0,
                    "factor": 2500,
                    "deckName": "Default",
                    "interval": 1,
                    "due": 0,
                    "reps": 1,
                    "fields": {"Front": {"value": "Q"}},
                }
            ],
            # getFSRSStats: difficulty=10 should become 1.0
            [{"cardId": 201, "difficulty": 10, "stability": 1.0}],
        ]
    )

    result = await repo.get_card_stats([2])
    assert result[0].fsrs is not None
    assert result[0].fsrs.difficulty == 1.0  # 10 / 10


@pytest.mark.asyncio
async def test_get_card_stats_fsrs_zero_difficulty(repo):
    """FSRS difficulty of 0 normalizes to 0.0."""
    repo._invoke = AsyncMock(
        side_effect=[
            [301],
            [
                {
                    "cardId": 301,
                    "note": 3,
                    "lapses": 0,
                    "factor": 2500,
                    "deckName": "Default",
                    "interval": 0,
                    "due": 0,
                    "reps": 0,
                    "fields": {},
                }
            ],
            [{"cardId": 301, "difficulty": 0, "stability": 5.0}],
        ]
    )

    result = await repo.get_card_stats([3])
    assert result[0].fsrs is not None
    assert result[0].fsrs.difficulty == 0.0


@pytest.mark.asyncio
async def test_get_card_stats_fsrs_unavailable_falls_back_to_cardsinfo(repo):
    """When getFSRSStats raises, falls back to difficulty from cardsInfo."""
    repo._invoke = AsyncMock(
        side_effect=[
            [401],  # findCards
            [  # cardsInfo with difficulty field
                {
                    "cardId": 401,
                    "note": 4,
                    "lapses": 0,
                    "factor": 2500,
                    "deckName": "Default",
                    "interval": 0,
                    "due": 0,
                    "reps": 0,
                    "difficulty": 7.0,  # Standard fallback
                    "fields": {"Front": {"value": "Fallback Q"}},
                }
            ],
            RuntimeError("getFSRSStats not available"),  # getFSRSStats fails
        ]
    )

    result = await repo.get_card_stats([4])

    assert len(result) == 1
    s = result[0]
    assert s.fsrs is not None
    assert s.fsrs.difficulty == 0.7  # 7.0 / 10.0
    assert s.fsrs.stability == 0  # Unknown when using fallback
    assert s.front == "Fallback Q"


@pytest.mark.asyncio
async def test_get_card_stats_no_fsrs_no_difficulty(repo):
    """When FSRS is unavailable AND cardsInfo has no difficulty, fsrs is None."""
    repo._invoke = AsyncMock(
        side_effect=[
            [501],  # findCards
            [  # cardsInfo without difficulty
                {
                    "cardId": 501,
                    "note": 5,
                    "lapses": 0,
                    "factor": 2500,
                    "deckName": "Default",
                    "interval": 0,
                    "due": 0,
                    "reps": 0,
                    "fields": {},
                }
            ],
            RuntimeError("getFSRSStats not available"),
        ]
    )

    result = await repo.get_card_stats([5])
    assert result[0].fsrs is None


@pytest.mark.asyncio
async def test_get_card_stats_missing_fields(repo):
    """When cardsInfo has no fields dict, front is None."""
    repo._invoke = AsyncMock(
        side_effect=[
            [601],
            [
                {
                    "cardId": 601,
                    "note": 6,
                    "lapses": 0,
                    "factor": 0,
                    "deckName": "Default",
                    "interval": 0,
                    "due": 0,
                    "reps": 0,
                }
            ],
            [],  # empty getFSRSStats
        ]
    )

    result = await repo.get_card_stats([6])
    assert result[0].front is None


@pytest.mark.asyncio
async def test_get_card_stats_multiple_nids(repo):
    """Multiple NIDs within a single chunk are all returned."""
    repo._invoke = AsyncMock(
        side_effect=[
            [101, 201],  # findCards for both
            [  # cardsInfo
                {
                    "cardId": 101,
                    "note": 1,
                    "lapses": 0,
                    "factor": 2500,
                    "deckName": "A",
                    "interval": 1,
                    "due": 0,
                    "reps": 1,
                    "fields": {"Front": {"value": "Q1"}},
                },
                {
                    "cardId": 201,
                    "note": 2,
                    "lapses": 1,
                    "factor": 2000,
                    "deckName": "B",
                    "interval": 7,
                    "due": 0,
                    "reps": 5,
                    "fields": {"Front": {"value": "Q2"}},
                },
            ],
            [],  # getFSRSStats empty
        ]
    )

    result = await repo.get_card_stats([1, 2])
    assert len(result) == 2
    assert result[0].front == "Q1"
    assert result[1].front == "Q2"


@pytest.mark.asyncio
async def test_get_card_stats_chunk_error_handled(repo):
    """When an entire chunk fails, it is skipped and does not crash."""
    repo._invoke = AsyncMock(side_effect=RuntimeError("Connection refused"))

    result = await repo.get_card_stats([1])
    assert result == []


@pytest.mark.asyncio
async def test_get_card_stats_default_values_for_missing_keys(repo):
    """Missing keys in cardsInfo fall back to defaults (0, 'Unknown', etc.)."""
    repo._invoke = AsyncMock(
        side_effect=[
            [701],
            [{"cardId": 701, "note": 7}],  # Minimal cardsInfo
            [],  # getFSRSStats empty
        ]
    )

    result = await repo.get_card_stats([7])
    assert len(result) == 1
    s = result[0]
    assert s.lapses == 0
    assert s.ease == 0
    assert s.interval == 0
    assert s.due == 0
    assert s.reps == 0
    assert s.deck_name == "Unknown"


# ---------- get_review_history ----------


@pytest.mark.asyncio
async def test_get_review_history_empty_cids(repo):
    """Empty CID list returns empty list without HTTP calls."""
    result = await repo.get_review_history([])
    assert result == []


@pytest.mark.asyncio
async def test_get_review_history_success(repo):
    """Review history entries are parsed and sorted by review_time."""
    repo._invoke = AsyncMock(
        return_value={
            "101": [
                {"id": 1700000000000, "ease": 3, "ivl": 7, "lastIvl": 1, "time": 5000, "type": 1},
                {"id": 1690000000000, "ease": 2, "ivl": 1, "lastIvl": 0, "time": 8000, "type": 0},
            ]
        }
    )

    result = await repo.get_review_history([101])

    assert len(result) == 2
    # Sorted by review_time ascending
    assert result[0].review_time < result[1].review_time
    assert result[0].rating == 2  # Earlier review
    assert result[1].rating == 3  # Later review
    assert result[0].card_id == 101
    assert result[0].time_taken == 8000


@pytest.mark.asyncio
async def test_get_review_history_error_handled(repo):
    """HTTP errors in review history return empty list."""
    repo._invoke = AsyncMock(side_effect=RuntimeError("Connection refused"))
    result = await repo.get_review_history([101])
    assert result == []


@pytest.mark.asyncio
async def test_get_review_history_none_result(repo):
    """None result from AnkiConnect returns empty list."""
    repo._invoke = AsyncMock(return_value=None)
    result = await repo.get_review_history([101])
    assert result == []


# ---------- get_deck_params ----------


@pytest.mark.asyncio
async def test_get_deck_params_success(repo):
    """Deck params are correctly extracted from getDeckConfig response."""
    repo._invoke = AsyncMock(
        return_value={
            "desiredRetention": 0.85,
            "sm2Retention": 0.88,
            "fsrs": {
                "desiredRetention": 0.85,
                "w": [0.4, 0.6, 2.4, 5.8],
            },
        }
    )

    result = await repo.get_deck_params(["MyDeck"])
    assert "MyDeck" in result
    assert result["MyDeck"]["desired_retention"] == 0.85
    assert result["MyDeck"]["weights"] == [0.4, 0.6, 2.4, 5.8]


@pytest.mark.asyncio
async def test_get_deck_params_fallback_on_error(repo):
    """When getDeckConfig fails, defaults are provided."""
    repo._invoke = AsyncMock(side_effect=RuntimeError("not available"))

    result = await repo.get_deck_params(["BadDeck"])
    assert "BadDeck" in result
    assert result["BadDeck"]["desired_retention"] == 0.9
    assert result["BadDeck"]["weights"] == []


@pytest.mark.asyncio
async def test_get_deck_params_null_response(repo):
    """When getDeckConfig returns None, defaults are provided."""
    repo._invoke = AsyncMock(return_value=None)

    result = await repo.get_deck_params(["NullDeck"])
    assert "NullDeck" in result
    assert result["NullDeck"]["desired_retention"] == 0.9


# ---------- close ----------


@pytest.mark.asyncio
async def test_close_without_client(repo):
    """Closing without ever making a request is a no-op."""
    await repo.close()  # Should not raise


@pytest.mark.asyncio
async def test_close_with_client(repo):
    """Closing after creating client clears it."""
    repo._client = AsyncMock()
    await repo.close()
    assert repo._client is None
