from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arete.interface.http_server import VERSION, app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == VERSION


@patch(
    "arete.composition.orchestrator.execute_sync", new_callable=AsyncMock
)  # Patched arete.composition.orchestrator.execute_sync
def test_sync_vault_endpoint(mock_sync, configured_vault):
    # Mock return value
    mock_stats = MagicMock()
    # Configure mock attributes to return primitives
    mock_stats.total_generated = 5
    mock_stats.total_imported = 5
    mock_stats.total_errors = 0
    mock_stats.prune = None
    mock_sync.return_value = mock_stats

    # SyncRequest via JSON
    response = client.post("/sync", json={"vault_root": str(configured_vault), "force": True})

    # Check execution
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["total_imported"] == 5

    mock_sync.assert_awaited_once()


@patch("arete.composition.orchestrator.execute_sync", new_callable=AsyncMock)
def test_sync_fail(mock_sync, configured_vault):
    mock_sync.side_effect = Exception("Boom")

    response = client.post("/sync", json={"vault_root": str(configured_vault)})

    assert response.status_code == 500
    assert "Boom" in response.json()["detail"]


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_suspend_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.suspend_cards.return_value = True
    mock_get_bridge.return_value = mock_bridge

    response = client.post("/anki/cards/suspend", json={"cids": [1, 2]})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_unsuspend_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.unsuspend_cards.return_value = True
    mock_get_bridge.return_value = mock_bridge

    response = client.post("/anki/cards/unsuspend", json={"cids": [1, 2]})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_styling_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.get_model_styling.return_value = "css"
    mock_get_bridge.return_value = mock_bridge

    response = client.get("/anki/models/Basic/styling")
    assert response.status_code == 200
    assert response.json()["css"] == "css"


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_templates_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.get_model_templates.return_value = {"C1": "T1"}
    mock_get_bridge.return_value = mock_bridge

    response = client.get("/anki/models/Basic/templates")
    assert response.status_code == 200
    assert response.json() == {"C1": "T1"}


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_gui_browse_endpoint(mock_get_bridge):
    mock_bridge = AsyncMock()
    mock_bridge.gui_browse.return_value = True
    mock_get_bridge.return_value = mock_bridge

    response = client.post("/anki/browse", json={"query": "deck:Default"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_suspend_cards_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.post("/anki/cards/suspend", json={"cids": [1]})
    assert response.status_code == 500
    assert "Bridge Fail" in response.json()["detail"]


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_unsuspend_cards_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.post("/anki/cards/unsuspend", json={"cids": [1]})
    assert response.status_code == 500
    assert "Bridge Fail" in response.json()["detail"]


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_get_model_styling_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.get("/anki/models/Basic/styling")
    assert response.status_code == 500


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
def test_get_model_templates_endpoint_fail(mock_get_anki):
    mock_get_anki.side_effect = Exception("Bridge Fail")
    response = client.get("/anki/models/Basic/templates")
    assert response.status_code == 500


@patch("arete.composition.factory.get_stats_repo")
def test_get_stats_endpoint_fail(mock_get_repo):
    mock_get_repo.side_effect = Exception("Repo Fail")
    response = client.post("/anki/stats", json={"nids": [1]})
    assert response.status_code == 500


@patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock)
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
    """With no vault configured, the server accepts no path and builds nothing."""
    response = client.post("/queue/build", json={})
    assert response.status_code == 400
    assert "No vault is configured" in response.json()["detail"]


def test_stats_closes_its_anki_client(monkeypatch):
    """Each /anki/stats request closes the HTTP client its AnkiConnect repository opened."""
    import httpx

    from arete.infrastructure.adapters.stats import connect_stats

    opened: list[httpx.AsyncClient] = []

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": [], "error": None})

    class RecordingClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(answer), **kwargs)
            opened.append(self)

    monkeypatch.setattr(connect_stats.httpx, "AsyncClient", RecordingClient)

    body = {"nids": [1], "backend": "ankiconnect", "anki_connect_url": "http://anki.test:1"}
    for _ in range(5):
        response = client.post("/anki/stats", json=body)
        assert response.status_code == 200
        assert response.json() == []

    assert len(opened) == 5
    assert [c for c in opened if not c.is_closed] == []


# --- Blocking work on the event loop (P11) ---
# Each test sends /health while a slow route runs, with the blocking piece stubbed to wait
# for /health. While the blocking piece runs on the event loop, /health waits for it.


async def _health_during(method: str, path: str, release, body: dict) -> list[str]:
    import asyncio

    import httpx

    finished: list[str] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        async def slow():
            response = await c.request(method, path, json=body)
            assert response.status_code == 200, response.text
            finished.append("slow")

        async def health():
            await asyncio.sleep(0.05)
            assert (await c.get("/health")).status_code == 200
            finished.append("health")
            release.set()

        await asyncio.gather(slow(), health())
    return finished


@pytest.mark.xfail(
    strict=True,
    reason="build_study_queue parses the vault on the event loop "
    "(application/queue/service.py build_graph calls); the route cannot move it to a thread",
)
@pytest.mark.asyncio
async def test_health_answers_during_a_queue_build(configured_vault):
    import threading

    from arete.domain.graph import DependencyGraph

    release = threading.Event()

    def blocking_graph(_vault_root):
        release.wait(timeout=0.5)
        return DependencyGraph()

    bridge = AsyncMock()
    bridge.get_due_cards.return_value = [1]
    bridge.map_nids_to_arete_ids.return_value = ["arete_A"]
    with (
        patch("arete.composition.factory.get_anki_bridge", AsyncMock(return_value=bridge)),
        patch("arete.application.queue.service.build_graph", blocking_graph),
    ):
        finished = await _health_during("POST", "/queue/build", release, {})
    assert finished == ["health", "slow"]


@pytest.mark.xfail(
    strict=True,
    reason="the sync pipeline reads files, parses YAML and writes sqlite on the event loop; "
    "the route cannot move it to a thread",
)
@pytest.mark.asyncio
async def test_health_answers_during_a_sync(configured_vault):
    import threading

    release = threading.Event()

    async def sync_with_a_blocking_piece(*_args, **_kwargs):
        release.wait(timeout=0.5)
        return MagicMock(total_generated=0, total_imported=0, total_errors=0, prune=None)

    with patch("arete.composition.orchestrator.execute_sync", sync_with_a_blocking_piece):
        finished = await _health_during("POST", "/sync", release, {})
    assert finished == ["health", "slow"]
