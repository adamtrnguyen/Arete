from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from arete.interface.http_server import VERSION, app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == VERSION


def test_get_version():
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": VERSION}


@patch("arete.application.orchestrator.execute_sync", new_callable=AsyncMock)  # Patched arete.application.orchestrator.execute_sync
def test_sync_vault_endpoint(mock_sync):
    # Mock return value
    mock_stats = MagicMock()
    # Configure mock attributes to return primitives
    mock_stats.total_generated = 5
    mock_stats.total_imported = 5
    mock_stats.total_errors = 0
    mock_sync.return_value = mock_stats

    # SyncRequest via JSON
    response = client.post("/sync", json={"vault_root": "/tmp/vault", "force": True})

    # Check execution
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["total_imported"] == 5

    mock_sync.assert_awaited_once()


@patch("arete.application.orchestrator.execute_sync", new_callable=AsyncMock)
def test_sync_fail(mock_sync):
    mock_sync.side_effect = Exception("Boom")

    response = client.post("/sync", json={"vault_root": "/tmp/vault"})

    assert response.status_code == 500
    assert "Boom" in response.json()["detail"]


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_suspend_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.suspend_cards.return_value = True
    mock_get_bridge.return_value = mock_bridge

    response = client.post("/anki/cards/suspend", json={"cids": [1, 2]})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_unsuspend_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.unsuspend_cards.return_value = True
    mock_get_bridge.return_value = mock_bridge

    response = client.post("/anki/cards/unsuspend", json={"cids": [1, 2]})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_styling_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.get_model_styling.return_value = "css"
    mock_get_bridge.return_value = mock_bridge

    response = client.get("/anki/models/Basic/styling")
    assert response.status_code == 200
    assert response.json()["css"] == "css"


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_templates_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.get_model_templates.return_value = {"C1": "T1"}
    mock_get_bridge.return_value = mock_bridge

    response = client.get("/anki/models/Basic/templates")
    assert response.status_code == 200
    assert response.json() == {"C1": "T1"}


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_gui_browse_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.gui_browse.return_value = True
    mock_get_bridge.return_value = mock_bridge

    response = client.post("/anki/browse", json={"query": "deck:Default"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_suspend_cards_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.post("/anki/cards/suspend", json={"cids": [1]})
    assert response.status_code == 500
    assert "Bridge Fail" in response.json()["detail"]


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_unsuspend_cards_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.post("/anki/cards/unsuspend", json={"cids": [1]})
    assert response.status_code == 500
    assert "Bridge Fail" in response.json()["detail"]


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_get_model_styling_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.get("/anki/models/Basic/styling")
    assert response.status_code == 500


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_get_model_templates_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.get("/anki/models/Basic/templates")
    assert response.status_code == 500


@patch("arete.application.factory.get_stats_repo")
def test_get_stats_endpoint_fail(mock_get_repo):
    mock_get_repo.side_effect = Exception("Repo Fail")
    response = client.post("/anki/stats", json={"nids": [1]})
    assert response.status_code == 500


@patch("arete.application.factory.get_anki_bridge", new_callable=AsyncMock)
def test_browse_anki_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.post("/anki/browse", json={"query": "test"})
    assert response.status_code == 500


@patch("os.kill")
def test_shutdown_endpoint(mock_kill):
    response = client.post("/shutdown")
    assert response.status_code == 200
    assert "shutting down" in response.json()["message"]
    # Wait for the thread to (potentially) call kill
    import time

    time.sleep(0.6)
    mock_kill.assert_called_once()


def test_build_queue_missing_vault_root():
    # We need to simulate the case where AppConfig.vault_root is None
    from arete.application.config import AppConfig

    with patch("arete.application.config.resolve_config") as mock_resolve:
        mock_resolve.return_value = AppConfig(vault_root=None)
        response = client.post("/queue/build", json={})
        assert response.status_code == 400
        assert "Vault root not configured" in response.json()["detail"]
