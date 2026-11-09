import asyncio
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from arete.composition import factory
from arete.infrastructure.adapters import anki_connect
from arete.interface.http_server import AnkiBridgePool, app, lifespan

client = TestClient(app)


@pytest.mark.asyncio
async def test_lifespan():
    app_mock = MagicMock()
    async with lifespan(app_mock):
        pass


def test_shutdown_api():
    with patch("threading.Thread") as mock_thread:
        response = client.post("/shutdown")
        assert response.status_code == 200
        mock_thread.return_value.start.assert_called_once()


# --- The shared AnkiConnect bridge (AnkiBridgePool) ---

URL_A = "http://anki-a.test:1"
URL_B = "http://anki-b.test:1"


class FakeAnki:
    """AnkiConnect behind a mock transport, and a stand-in for the direct adapter."""

    def __init__(self):
        """Anki starts up, with nothing called yet."""
        self.up = True
        self.calls: list[tuple[str, str]] = []  # (host, action)
        self.clients: list[httpx.AsyncClient] = []
        self.direct_built = 0
        self.direct_closed = 0

    def answer(self, request: httpx.Request) -> httpx.Response:
        action = json.loads(request.content)["action"]
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        self.calls.append((request.url.host, action))
        result = {
            "version": 6,
            "deckNames": [f"deck@{request.url.host}"],
            "modelStyling": {"css": "connect-css"},
        }.get(action)
        return httpx.Response(200, json={"result": result, "error": None})

    def probes(self) -> int:
        return sum(1 for _, action in self.calls if action == "version")


@pytest.fixture
def anki(monkeypatch):
    fake = FakeAnki()

    class RecordingClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(fake.answer), **kwargs)
            fake.clients.append(self)

    class FakeDirect:
        def __init__(self, anki_base=None):
            fake.direct_built += 1

        async def get_deck_names(self):
            return ["direct-deck"]

        async def get_model_styling(self, name):
            return "direct-css"

        async def close(self):
            fake.direct_closed += 1

    monkeypatch.setattr(anki_connect.httpx, "AsyncClient", RecordingClient)
    monkeypatch.setattr(factory, "AnkiDirectAdapter", FakeDirect)
    return fake


def _decks(c: TestClient, url: str = URL_A, **extra) -> list[str]:
    response = c.post("/anki/decks", json={"anki_connect_url": url, **extra})
    assert response.status_code == 200, response.text
    return response.json()["decks"]


def test_ankiconnect_bridge_is_built_once_across_requests(anki):
    with TestClient(app) as c:
        for _ in range(5):
            assert _decks(c) == ["deck@anki-a.test"]
            css = c.get("/anki/models/Basic/styling", params={"anki_connect_url": URL_A})
            assert css.json() == {"css": "connect-css"}
        assert anki.probes() == 1
        assert len(anki.clients) == 1
        assert not anki.clients[0].is_closed
    # The lifespan's shutdown closes it.
    assert anki.clients[0].is_closed


def test_direct_bridge_is_never_reused(anki):
    anki.up = False
    with TestClient(app) as c:
        for _ in range(3):
            assert _decks(c) == ["direct-deck"]
    assert anki.direct_built == 3
    assert anki.direct_closed == 3


def test_backend_direct_bypasses_the_shared_bridge(anki):
    with TestClient(app) as c:
        assert _decks(c) == ["deck@anki-a.test"]
        assert _decks(c, backend="direct") == ["direct-deck"]
        assert _decks(c) == ["deck@anki-a.test"]
    assert anki.direct_built == 1
    assert anki.probes() == 1


def test_anki_quit_then_restarted(anki):
    with TestClient(app) as c:
        assert _decks(c) == ["deck@anki-a.test"]

        anki.up = False  # Anki quits: the shared bridge's call fails, the request goes direct.
        assert _decks(c) == ["direct-deck"]
        assert _decks(c) == ["direct-deck"]  # resolved again, not reused
        assert anki.direct_built == 2

        anki.up = True  # Anki is back: the next request shares a new AnkiConnect bridge.
        assert _decks(c) == ["deck@anki-a.test"]
        assert _decks(c) == ["deck@anki-a.test"]
    assert anki.probes() == 2  # answered probes: the first request, and the one after the restart
    shared = [anki.clients[0], anki.clients[-1]]
    assert all(client.is_closed for client in shared)


def test_a_failure_the_adapter_swallows_still_falls_back(anki):
    """get_model_styling returns "" when Anki is unreachable instead of raising."""
    with TestClient(app) as c:
        params = {"anki_connect_url": URL_A}
        assert c.get("/anki/models/Basic/styling", params=params).json() == {"css": "connect-css"}
        anki.up = False
        assert c.get("/anki/models/Basic/styling", params=params).json() == {"css": "direct-css"}


def test_each_request_reaches_the_anki_it_names(anki):
    with TestClient(app) as c:
        assert _decks(c, URL_A) == ["deck@anki-a.test"]
        assert _decks(c, URL_B) == ["deck@anki-b.test"]
        assert _decks(c, URL_A) == ["deck@anki-a.test"]
    hosts = [host for host, action in anki.calls if action == "deckNames"]
    assert hosts == ["anki-a.test", "anki-b.test", "anki-a.test"]
    # The replaced bridge is closed once no request holds it.
    assert len(anki.clients) == 3
    assert all(client.is_closed for client in anki.clients)


def test_without_the_lifespan_each_request_builds_its_own_bridge(anki):
    c = TestClient(app)  # no `with`: the lifespan does not run
    for _ in range(3):
        assert _decks(c) == ["deck@anki-a.test"]
    assert anki.probes() == 3
    assert all(client.is_closed for client in anki.clients)


class FakeConnect:
    """A shared-able bridge whose calls can be held open."""

    built = 0

    def __init__(self):
        """Count the build; the bridge answers until a test says otherwise."""
        FakeConnect.built += 1
        self.responsive = True
        self.closed = False
        self.release = asyncio.Event()

    async def is_responsive(self):
        return self.responsive

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_connect(monkeypatch):
    FakeConnect.built = 0

    async def resolve(_config):
        await asyncio.sleep(0.01)  # concurrent requests overlap here
        return FakeConnect()

    monkeypatch.setattr(factory, "AnkiConnectAdapter", FakeConnect)
    monkeypatch.setattr(factory, "get_anki_bridge", resolve)


def _config(url: str = URL_A):
    config = MagicMock()
    config.backend = "auto"
    config.anki_connect_url = url
    return config


@pytest.mark.asyncio
async def test_concurrent_requests_build_one_bridge(fake_connect):
    pool = AnkiBridgePool()

    async def op(bridge):
        await asyncio.sleep(0)
        return id(bridge)

    ids = await asyncio.gather(*(pool.run(_config(), op) for _ in range(10)))
    assert FakeConnect.built == 1
    assert len(set(ids)) == 1
    await pool.close()


@pytest.mark.asyncio
async def test_a_dropped_bridge_is_not_closed_while_in_use(fake_connect):
    pool = AnkiBridgePool()
    first = await pool.run(_config(), lambda bridge: asyncio.sleep(0, result=bridge))

    async def slow(bridge):
        await bridge.release.wait()
        return "slow done"

    slow_task = asyncio.create_task(pool.run(_config(), slow))
    await asyncio.sleep(0)

    first.responsive = False  # Anki quits; another request finds out and drops the bridge.

    async def failing(bridge):
        if bridge is first:
            raise ConnectionError("refused")
        return "fresh"

    assert await pool.run(_config(), failing) == "fresh"
    assert not first.closed  # the slow request still holds it

    first.release.set()
    assert await slow_task == "slow done"
    assert first.closed
    await pool.close()


@pytest.mark.asyncio
async def test_a_genuine_error_on_a_live_bridge_is_not_retried(fake_connect):
    pool = AnkiBridgePool()
    await pool.run(_config(), lambda bridge: asyncio.sleep(0))
    calls = 0

    async def boom(_bridge):
        nonlocal calls
        calls += 1
        raise ValueError("bad model")

    with pytest.raises(ValueError):
        await pool.run(_config(), boom)
    assert calls == 1
    assert FakeConnect.built == 1
    await pool.close()
