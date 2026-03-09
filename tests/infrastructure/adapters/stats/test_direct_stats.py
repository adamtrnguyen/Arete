"""Tests for DirectStatsRepository — SQLite-based stats fetching."""

from unittest.mock import MagicMock, patch

import pytest

from arete.infrastructure.adapters.stats import DirectStatsRepository


@pytest.fixture
def mock_repo():
    """Patch AnkiRepository so no real Anki collection is needed."""
    with patch("arete.infrastructure.adapters.stats.direct_stats.AnkiRepository") as MockRepo:
        mock_instance = MockRepo.return_value
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.col = MagicMock()
        mock_instance.col.db = MagicMock()
        yield mock_instance


def _make_card(cid=101, nid=1, lapses=0, factor=2500, ivl=10, due=0, reps=5, did=1):
    """Create a mock card object."""
    card = MagicMock()
    card.id = cid
    card.nid = nid
    card.lapses = lapses
    card.factor = factor
    card.ivl = ivl
    card.due = due
    card.reps = reps
    card.did = did
    card.memory_state = None  # No FSRS by default
    return card


# ---------- get_card_stats ----------


@pytest.mark.asyncio
async def test_get_card_stats_empty_nids(mock_repo):
    """Empty NID list returns empty list immediately."""
    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([])
    assert result == []


@pytest.mark.asyncio
async def test_get_card_stats_no_collection(mock_repo):
    """When collection can't be opened, returns empty list."""
    mock_repo.col = None
    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([1])
    assert result == []


@pytest.mark.asyncio
async def test_get_card_stats_basic(mock_repo):
    """Basic stats fetching with a single NID."""
    card = _make_card(cid=101, nid=1, lapses=3, factor=2500, ivl=14, due=1700000000, reps=20)
    mock_repo.col.find_cards.return_value = [101]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Science"}

    note = MagicMock()
    note.fields = ["What is DNA?", "Deoxyribonucleic acid"]
    mock_repo.col.get_note.return_value = note

    # Mock helper queries (revlog)
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([1])

    assert len(result) == 1
    s = result[0]
    assert s.card_id == 101
    assert s.note_id == 1
    assert s.lapses == 3
    assert s.ease == 2500
    assert s.deck_name == "Science"
    assert s.interval == 14
    assert s.due == 1700000000
    assert s.reps == 20
    assert s.front == "What is DNA?"
    assert s.fsrs is None  # No memory_state set


@pytest.mark.asyncio
async def test_get_card_stats_with_fsrs_memory_state(mock_repo):
    """FSRS memory state is extracted when card.memory_state is present."""
    card = _make_card(cid=201, nid=2)
    ms = MagicMock()
    ms.difficulty = 7.5
    ms.stability = 30.0
    card.memory_state = ms

    mock_repo.col.find_cards.return_value = [201]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([2])

    assert len(result) == 1
    s = result[0]
    assert s.fsrs is not None
    assert s.fsrs.difficulty == 7.5  # Direct uses native 1-10 scale
    assert s.fsrs.stability == 30.0
    assert s.fsrs.retrievability is None  # Computed by application layer


@pytest.mark.asyncio
async def test_get_card_stats_fsrs_missing_difficulty(mock_repo):
    """When memory_state exists but difficulty is missing, fsrs is None."""
    card = _make_card(cid=301, nid=3)
    ms = MagicMock(spec=[])  # Empty spec, no attributes
    card.memory_state = ms

    mock_repo.col.find_cards.return_value = [301]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([3])

    # hasattr checks fail on empty spec, so fsrs should be None
    assert result[0].fsrs is None


@pytest.mark.asyncio
async def test_get_card_stats_fsrs_none_values(mock_repo):
    """When memory_state has None difficulty or stability, fsrs is None."""
    card = _make_card(cid=401, nid=4)
    ms = MagicMock()
    ms.difficulty = None
    ms.stability = None
    card.memory_state = ms

    mock_repo.col.find_cards.return_value = [401]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([4])

    assert result[0].fsrs is None


@pytest.mark.asyncio
async def test_get_card_stats_deck_missing(mock_repo):
    """When deck lookup returns None, deck_name is 'Unknown'."""
    card = _make_card(cid=501, nid=5, did=9999)
    mock_repo.col.find_cards.return_value = [501]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = None
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([5])

    assert result[0].deck_name == "Unknown"


@pytest.mark.asyncio
async def test_get_card_stats_note_fields_empty(mock_repo):
    """When note has empty fields list, front is None."""
    card = _make_card(cid=601, nid=6)
    mock_repo.col.find_cards.return_value = [601]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=[])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([6])

    assert result[0].front is None


@pytest.mark.asyncio
async def test_get_card_stats_note_error_handled(mock_repo):
    """Exception when fetching note doesn't crash; front is None."""
    card = _make_card(cid=701, nid=7)
    mock_repo.col.find_cards.return_value = [701]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.side_effect = Exception("Note not found")
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([7])

    assert result[0].front is None


@pytest.mark.asyncio
async def test_get_card_stats_last_review_from_revlog(mock_repo):
    """Last review time is extracted from revlog MAX(id)."""
    card = _make_card(cid=801, nid=8)
    mock_repo.col.find_cards.return_value = [801]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    # MAX(id) from revlog (ms timestamp) -> divided by 1000
    mock_repo.col.db.scalar.side_effect = [1700000000000, 5000]  # last_review, avg_time
    mock_repo.col.db.execute.return_value = []  # answer_distribution

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([8])

    assert result[0].last_review == 1700000000  # ms -> seconds


@pytest.mark.asyncio
async def test_get_card_stats_answer_distribution(mock_repo):
    """Answer distribution is correctly built from revlog grouping."""
    card = _make_card(cid=901, nid=9)
    mock_repo.col.find_cards.return_value = [901]
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    mock_repo.col.db.scalar.return_value = None

    # answer_distribution query returns tuples (ease, count)
    mock_repo.col.db.execute.return_value = [(1, 2), (3, 10), (4, 5)]

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([9])

    dist = result[0].answer_distribution
    assert dist[1] == 2  # Again x2
    assert dist[3] == 10  # Good x10
    assert dist[4] == 5  # Easy x5


@pytest.mark.asyncio
async def test_get_card_stats_multiple_cards_per_note(mock_repo):
    """A note with multiple cards returns stats for each card."""
    card_a = _make_card(cid=1001, nid=10)
    card_b = _make_card(cid=1002, nid=10, lapses=1)

    mock_repo.col.find_cards.return_value = [1001, 1002]
    mock_repo.col.get_card.side_effect = [card_a, card_b]
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q"])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([10])

    assert len(result) == 2
    assert result[0].card_id == 1001
    assert result[1].card_id == 1002
    assert result[1].lapses == 1


@pytest.mark.asyncio
async def test_get_card_stats_error_per_nid_handled(mock_repo):
    """Exception on one NID doesn't prevent others from being fetched."""
    mock_repo.col.find_cards.side_effect = [Exception("bad nid"), [201]]

    card = _make_card(cid=201, nid=2)
    mock_repo.col.get_card.return_value = card
    mock_repo.col.decks.get.return_value = {"name": "Default"}
    mock_repo.col.get_note.return_value = MagicMock(fields=["Q2"])
    mock_repo.col.db.scalar.return_value = None
    mock_repo.col.db.execute.return_value = []

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_card_stats([1, 2])

    assert len(result) == 1
    assert result[0].card_id == 201


# ---------- get_review_history ----------


@pytest.mark.asyncio
async def test_get_review_history_empty_cids(mock_repo):
    """Empty CID list returns empty list immediately."""
    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_review_history([])
    assert result == []


@pytest.mark.asyncio
async def test_get_review_history_no_collection(mock_repo):
    """When collection can't be opened, returns empty list."""
    mock_repo.col = None
    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_review_history([101])
    assert result == []


@pytest.mark.asyncio
async def test_get_review_history_basic(mock_repo):
    """Review history entries are parsed correctly from revlog rows."""
    # Simulate PRAGMA returning columns without 'data'
    mock_repo.col.db.execute.side_effect = [
        [
            (0, "id", "", 0, None, 0),
            (1, "cid", "", 0, None, 0),
            (2, "ease", "", 0, None, 0),
            (3, "ivl", "", 0, None, 0),
            (4, "lastIvl", "", 0, None, 0),
            (5, "time", "", 0, None, 0),
            (6, "type", "", 0, None, 0),
        ],  # PRAGMA table_info
        [  # SELECT query results (id, cid, ease, ivl, lastIvl, time, type)
            (1700000000000, 101, 3, 7, 1, 5000, 1),
            (1690000000000, 101, 2, 1, 0, 8000, 0),
        ],
    ]

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_review_history([101])

    assert len(result) == 2
    assert result[0].card_id == 101
    assert result[0].rating == 3
    assert result[0].interval == 7
    assert result[0].time_taken == 5000
    assert result[0].review_time == 1700000000  # ms to seconds


@pytest.mark.asyncio
async def test_get_review_history_with_fsrs_data(mock_repo):
    """When revlog has 'data' column with FSRS JSON, it is extracted."""
    import json

    fsrs_data = json.dumps({"s": 15.0, "d": 5.0, "r": 0.92})

    mock_repo.col.db.execute.side_effect = [
        [
            (0, "id", "", 0, None, 0),
            (1, "cid", "", 0, None, 0),
            (2, "ease", "", 0, None, 0),
            (3, "ivl", "", 0, None, 0),
            (4, "lastIvl", "", 0, None, 0),
            (5, "time", "", 0, None, 0),
            (6, "type", "", 0, None, 0),
            (7, "data", "", 0, None, 0),
        ],  # has 'data' column
        [  # SELECT results with data column
            (1700000000000, 101, 3, 7, 1, 5000, 1, fsrs_data),
        ],
    ]

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_review_history([101])

    assert len(result) == 1
    assert result[0].stability == 15.0
    assert result[0].difficulty == 5.0
    assert result[0].retrievability == 0.92


# ---------- get_deck_params ----------


@pytest.mark.asyncio
async def test_get_deck_params_no_collection(mock_repo):
    """When collection can't be opened, returns empty dict."""
    mock_repo.col = None
    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_deck_params(["MyDeck"])
    assert result == {}


@pytest.mark.asyncio
async def test_get_deck_params_success(mock_repo):
    """Deck FSRS params are correctly extracted from config."""
    deck = {"name": "MyDeck", "conf": 1}
    mock_repo.col.decks.by_name.return_value = deck
    config = {
        "desiredRetention": 0.85,
        "sm2Retention": 0.88,
        "fsrs": {
            "desiredRetention": 0.85,
            "w": [0.4, 0.6, 2.4, 5.8],
        },
    }
    mock_repo.col.decks.get_config.return_value = config

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_deck_params(["MyDeck"])

    assert "MyDeck" in result
    assert result["MyDeck"]["desired_retention"] == 0.85
    assert result["MyDeck"]["weights"] == [0.4, 0.6, 2.4, 5.8]


@pytest.mark.asyncio
async def test_get_deck_params_deck_not_found(mock_repo):
    """When deck doesn't exist, it is silently skipped."""
    mock_repo.col.decks.by_name.return_value = None

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_deck_params(["NonExistent"])

    assert result == {}


@pytest.mark.asyncio
async def test_get_deck_params_config_not_found(mock_repo):
    """When config lookup returns None, deck is skipped."""
    mock_repo.col.decks.by_name.return_value = {"name": "X", "conf": 99}
    mock_repo.col.decks.get_config.return_value = None

    repo = DirectStatsRepository(anki_base=None)
    result = await repo.get_deck_params(["X"])

    assert result == {}
