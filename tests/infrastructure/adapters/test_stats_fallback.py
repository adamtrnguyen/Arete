from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from arete.domain.stats.models import CardStatsAggregate, FsrsMemoryState
from arete.interface.http_server import app


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
        "arete.composition.factory.get_stats_repo",
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
    """Verify POST /anki/cards/suspend calls bridge.suspend_cards with config overrides."""
    client = TestClient(app)
    mock_bridge = AsyncMock()
    mock_bridge.suspend_cards.return_value = True

    with patch(
        "arete.composition.factory.get_anki_bridge",
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
    """Verify GET /anki/models/{name}/templates calls bridge."""
    client = TestClient(app)
    mock_bridge = AsyncMock()
    mock_bridge.get_model_templates.return_value = {"Card 1": {"Front": "F", "Back": "B"}}

    with patch(
        "arete.composition.factory.get_anki_bridge",
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
