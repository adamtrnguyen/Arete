"""One adapter serves concurrent requests over one HTTP client.

The HTTP server shares one adapter across its requests, so a request that times out must
not break the connections of the requests still in flight beside it. These tests run the
real httpx client against a local server that answers as the add-on does: one reply per
connection, then it closes.
"""

import asyncio
import contextlib
import json

import httpx
import pytest
import pytest_asyncio

from arete.domain.models import OutcomeUnknown
from arete.infrastructure.adapters import anki_connect
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

READERS = 4


class _Server:
    """Holds every ``deckNames`` reply until released; never answers ``addNote``."""

    def __init__(self):
        """Nothing has arrived yet."""
        self.arrived: list[str] = []
        self.readers_in = asyncio.Event()
        self.add_in = asyncio.Event()
        self.add_dropped = asyncio.Event()  # the client closed the addNote connection
        self.release = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        head = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":")[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        action = json.loads(await reader.readexactly(length))["action"]
        self.arrived.append(action)
        if action == "addNote":
            self.add_in.set()
            await reader.read()  # returns when the client closes the connection
            self.add_dropped.set()
            writer.close()
            return
        if self.arrived.count("deckNames") == READERS:
            self.readers_in.set()
        await self.release.wait()
        body = json.dumps({"result": ["Default"], "error": None}).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()


@pytest_asyncio.fixture
async def served(monkeypatch):
    clients: list[httpx.AsyncClient] = []

    class RecordingClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            clients.append(self)

    monkeypatch.setattr(anki_connect.httpx, "AsyncClient", RecordingClient)
    monkeypatch.setattr(anki_connect, "_RETRY_DELAY", 0.0)
    server = _Server()
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    adapter = AnkiConnectAdapter(url=f"http://127.0.0.1:{port}")
    yield adapter, server, clients
    await adapter.close()
    tcp.close()
    await tcp.wait_closed()


def _time_out_the_add(adapter: AnkiConnectAdapter, server: _Server) -> None:
    """``addNote`` times out as ``asyncio.wait_for`` does: its request is cancelled."""
    send = adapter._http_request

    async def http(payload: dict):
        if payload["action"] != "addNote":
            return await send(payload)
        request = asyncio.ensure_future(send(payload))
        await server.add_in.wait()
        request.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await request
        raise TimeoutError

    adapter._http_request = http  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_a_timeout_does_not_break_the_requests_in_flight_beside_it(served):
    adapter, server, clients = served
    _time_out_the_add(adapter, server)

    readers = [asyncio.ensure_future(adapter._invoke("deckNames")) for _ in range(READERS)]
    await asyncio.wait_for(server.readers_in.wait(), 5)
    with pytest.raises(OutcomeUnknown):
        await adapter._invoke("addNote", note={"deckName": "D"})
    server.release.set()
    results = await asyncio.wait_for(asyncio.gather(*readers, return_exceptions=True), 5)

    assert results == [["Default"]] * READERS
    # The timed-out request's own connection is dropped, and no client was replaced.
    await asyncio.wait_for(server.add_dropped.wait(), 5)
    assert len(clients) == 1 and adapter._client is clients[0]
    assert not clients[0].is_closed


@pytest.mark.asyncio
async def test_close_releases_the_client_after_a_timeout(served):
    adapter, server, clients = served
    _time_out_the_add(adapter, server)

    reader = asyncio.ensure_future(adapter._invoke("deckNames"))
    with pytest.raises(OutcomeUnknown):
        await adapter._invoke("addNote", note={"deckName": "D"})
    server.release.set()
    assert await asyncio.wait_for(reader, 5) == ["Default"]
    await adapter.close()

    assert adapter._client is None
    assert len(clients) == 1
    assert all(client.is_closed for client in clients)
