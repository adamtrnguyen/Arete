"""The suite cannot reach a running Anki, whatever the machine has open.

`tests/conftest.py` refuses sockets to port 8765. These tests pin that guard: if it is
ever removed, a default config with Anki open would select AnkiConnect and sync into
the user's real collection.
"""

import socket

import pytest

from arete.application.config import resolve_config
from arete.composition.factory import get_anki_bridge
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_socket_to_ankiconnect_is_refused(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(ConnectionRefusedError):
            s.connect((host, 8765))


@pytest.mark.asyncio
async def test_auto_backend_never_selects_a_live_ankiconnect(_isolated_home):
    bridge = await get_anki_bridge(resolve_config())
    try:
        assert isinstance(bridge, AnkiDirectAdapter)
        assert str(_isolated_home) in str(bridge.anki_base)
    finally:
        await bridge.close()
