from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from arete.interface.http_server import app

client = TestClient(app)


@patch("arete.application.factory.get_anki_bridge")
@pytest.mark.asyncio
async def test_server_get_decks(mock_bridge_factory):
    mock_anki = AsyncMock()
    mock_anki.get_deck_names.return_value = ["Deck A", "Deck B"]
    mock_bridge_factory.return_value = mock_anki

    response = client.post("/anki/decks", json={})
    assert response.status_code == 200
    assert response.json()["decks"] == ["Deck A", "Deck B"]


@patch("arete.application.factory.get_anki_bridge")
@patch("arete.application.queue.builder.build_simple_queue")
@patch("arete.application.queue.graph_resolver.build_graph")
@pytest.mark.asyncio
async def test_server_build_queue(mock_build_graph, mock_build_queue, mock_bridge_factory):
    # Mock Anki Bridge
    mock_anki = AsyncMock()
    mock_anki.get_due_cards.return_value = [101, 102]
    mock_anki.map_nids_to_arete_ids.return_value = ["arete_A", "arete_B"]
    mock_bridge_factory.return_value = mock_anki

    # Mock Graph
    from arete.domain.graph import CardNode, DependencyGraph

    graph = DependencyGraph()
    graph.nodes["arete_A"] = CardNode(id="arete_A", title="Card A", file_path="a.md", line_number=1)
    graph.nodes["arete_B"] = CardNode(id="arete_B", title="Card B", file_path="b.md", line_number=1)
    graph.nodes["arete_P"] = CardNode(
        id="arete_P", title="Prereq P", file_path="p.md", line_number=1
    )
    mock_build_graph.return_value = graph

    # Mock Queue Result
    from arete.application.queue.builder import QueueBuildResult

    mock_build_queue.return_value = QueueBuildResult(
        prereq_queue=["arete_P"],
        main_queue=["arete_A", "arete_B"],
        skipped_strong=[],
        missing_prereqs=[],
        cycles=[],
    )

    response = client.post("/queue/build", json={"deck": "TestDeck"})

    assert response.status_code == 200
    data = response.json()
    assert data["deck"] == "TestDeck"
    assert data["due_count"] == 2
    assert data["total_with_prereqs"] == 3

    queue = data["queue"]
    assert len(queue) == 3
    assert queue[0]["id"] == "arete_P"
    assert queue[0]["is_prereq"] is True
    assert queue[1]["id"] == "arete_A"
    assert queue[1]["is_prereq"] is False


@patch("arete.application.factory.get_anki_bridge")
@pytest.mark.asyncio
async def test_server_create_queue_deck(mock_bridge_factory):
    response = client.post("/queue/create-deck", json={"card_ids": ["arete_A"]})
    assert response.status_code == 200
    assert response.json()["ok"] is True
