from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arete.domain.stats.models import CardStatsAggregate, FsrsMemoryState
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from arete.interface.http_server import app


# Mock Anki Repository for Direct Adapter
@pytest.fixture
def mock_repo():
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository") as MockRepo:
        mock_instance = MockRepo.return_value
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.col = MagicMock()
        yield mock_instance


@pytest.mark.asyncio
async def test_direct_adapter_get_stats(mock_repo):
    adapter = AnkiDirectAdapter(anki_base=None)

    # Mock find_cards
    mock_repo.col.find_cards.side_effect = lambda q: [101] if "nid:1" in q else []

    # Mock get_card
    mock_card = MagicMock()
    mock_card.id = 101
    mock_card.nid = 1
    mock_card.lapses = 5
    mock_card.factor = 2500
    mock_card.ivl = 10
    mock_card.due = 1234567890
    mock_card.reps = 20
    mock_card.did = 1
    # Mock FSRS memory state (v3 scheduler)
    mock_card.memory_state = MagicMock()
    mock_card.memory_state.difficulty = 8.5  # 0-10 scale

    mock_repo.col.get_card.return_value = mock_card

    # Mock decks
    mock_repo.col.decks.get.return_value = {"name": "Default"}

    # Mock get_note for Front field
    mock_note = MagicMock()
    mock_note.fields = ["Front Content", "Back Content"]
    mock_repo.col.get_note.return_value = mock_note

    stats = await adapter.get_card_stats([1])

    assert len(stats) == 1
    s = stats[0]
    assert s.card_id == 101
    assert s.note_id == 1
    assert s.lapses == 5
    assert s.difficulty == 0.85  # Normalized
    assert s.front == "Front Content"


@pytest.mark.asyncio
async def test_connect_adapter_get_stats():
    adapter = AnkiConnectAdapter()

    # Mock _invoke
    adapter._invoke = AsyncMock()

    # 1. findCards
    adapter._invoke.side_effect = [
        [101],  # findCards result
        [
            {  # cardsInfo result
                "cardId": 101,
                "note": 1,
                "lapses": 2,
                "factor": 2600,
                "deckName": "Test::Deck",
                "interval": 5,
                "due": 9999,
                "reps": 10,
                "difficulty": 0.0,  # Standard fallback
                "fields": {"Front": {"value": "Connect Front"}},
            }
        ],
        [  # getFSRSStats result
            {"cardId": 101, "difficulty": 7.5}
        ],
    ]

    # Note: side_effect iterates.
    # Logic: findCards -> cardsInfo -> getFSRSStats

    stats = await adapter.get_card_stats([1])

    assert len(stats) == 1
    s = stats[0]
    assert s.card_id == 101
    assert s.difficulty == 0.75  # FSRS preference
    assert s.front == "Connect Front"


def test_server_stats_endpoint():
    client = TestClient(app)

    mock_repo = MagicMock()
    mock_repo.get_card_stats = AsyncMock(
        return_value=[
            CardStatsAggregate(
                card_id=1,
                note_id=1,
                deck_name="D",
                lapses=0,
                ease=0,
                interval=0,
                due=0,
                reps=0,
                fsrs=FsrsMemoryState(stability=1.0, difficulty=0.5, retrievability=0.9),
                last_review=12345678,
                front="F",
            )
        ]
    )
    mock_repo.get_review_history = AsyncMock(return_value=[])
    mock_repo.get_deck_params = AsyncMock(return_value={})

    with patch(
        "arete.application.factory.get_stats_repo",
        return_value=mock_repo,
    ):
        # We also need to patch resolve_config to avoid loading real config
        with patch("arete.application.config.resolve_config", return_value=MagicMock()):
            res = client.post("/anki/stats", json={"nids": [1]})
            assert res.status_code == 200
            data = res.json()
            assert len(data) == 1
            assert data[0]["front"] == "F"


def test_server_suspend_endpoint():
    """Verify POST /anki/cards/suspend calls bridge.suspend_cards with config overrides"""
    client = TestClient(app)
    mock_bridge = AsyncMock()
    mock_bridge.suspend_cards.return_value = True

    with patch(
        "arete.application.factory.get_anki_bridge",
        new=AsyncMock(return_value=mock_bridge),
    ):
        with patch(
            "arete.application.config.resolve_config", return_value=MagicMock()
        ) as mock_resolve:
            # Test with override
            resp = client.post(
                "/anki/cards/suspend", json={"cids": [100, 200], "backend": "direct"}
            )
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

            # Verify config injection
            # resolve_config should be called with overrides
            mock_resolve.assert_called_with({"backend": "direct"})

            mock_bridge.suspend_cards.assert_awaited_once_with([100, 200])


def test_server_model_templates_endpoint():
    """Verify GET /anki/models/{name}/templates calls bridge"""
    client = TestClient(app)
    mock_bridge = AsyncMock()
    mock_bridge.get_model_templates.return_value = {"Card 1": {"Front": "F", "Back": "B"}}

    with patch(
        "arete.application.factory.get_anki_bridge",
        new=AsyncMock(return_value=mock_bridge),
    ):
        with patch(
            "arete.application.config.resolve_config", return_value=MagicMock()
        ) as mock_resolve:
            resp = client.get("/anki/models/Basic/templates?backend=ankiconnect")
            assert resp.status_code == 200
            data = resp.json()
            assert data["Card 1"]["Front"] == "F"

            # Verify config injection from query param
            mock_resolve.assert_called_with({"backend": "ankiconnect"})

            mock_bridge.get_model_templates.assert_awaited_once_with("Basic")
