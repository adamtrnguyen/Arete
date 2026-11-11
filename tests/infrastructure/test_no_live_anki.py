"""The suite cannot reach a running Anki, whatever the machine has open.

`tests/conftest.py` refuses sockets to port 8765. These tests pin that guard: if it is
ever removed, a default config with Anki open would select AnkiConnect and sync into
the user's real collection. The one exception, an integration test reaching the address
ANKI_CONNECT_URL names, is pinned below.
"""

import socket
from pathlib import Path

import pytest

from arete.application.config import resolve_config
from arete.composition.factory import get_anki_bridge
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from tests import conftest
from tests.conftest import ankiconnect_allowed, ankiconnect_refused, configured_anki_addresses


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


# --- The guard's rule: who may reach port 8765 ---


class _Node:
    """A test item as the guard sees it: only its markers."""

    def __init__(self, *markers: str):
        self.markers = set(markers)

    def get_closest_marker(self, name: str):
        return name if name in self.markers else None


HOST_MODE_URL = {"ANKI_CONNECT_URL": "http://127.0.0.1:8765"}


def test_a_unit_test_is_refused_even_when_the_integration_url_is_8765():
    allowed = ankiconnect_allowed(_Node(), HOST_MODE_URL)
    assert allowed == frozenset()
    assert ankiconnect_refused(("127.0.0.1", 8765), allowed)


def test_an_integration_test_reaches_exactly_the_configured_address():
    allowed = ankiconnect_allowed(_Node("integration"), HOST_MODE_URL)
    assert not ankiconnect_refused(("127.0.0.1", 8765), allowed)
    assert ankiconnect_refused(("127.0.0.2", 8765), allowed)
    assert ankiconnect_refused(("::1", 8765, 0, 0), allowed)


def test_an_integration_test_with_no_configured_url_is_refused():
    assert ankiconnect_allowed(_Node("integration"), {}) == frozenset()
    assert ankiconnect_refused(("127.0.0.1", 8765), frozenset())


def test_a_configured_host_name_allows_what_it_resolves_to():
    allowed = configured_anki_addresses("http://localhost:8765")
    assert ("localhost", 8765) in allowed
    assert ("127.0.0.1", 8765) in allowed or ("::1", 8765) in allowed


def test_only_port_8765_is_guarded():
    assert not ankiconnect_refused(("127.0.0.1", 8766), frozenset())
    assert not ankiconnect_refused("/tmp/a.sock", frozenset())


def test_only_tests_under_tests_integration_are_marked_integration():
    class Item:
        def __init__(self, path: Path):
            self.path = path
            self.marks: list[str] = []

        def add_marker(self, mark):
            self.marks.append(mark.name)

    root = Path(conftest.__file__).parent
    inside = Item(root / "integration" / "test_x.py")
    outside = Item(root / "infrastructure" / "integration_like.py")
    conftest.pytest_collection_modifyitems([inside, outside])
    assert (inside.marks, outside.marks) == (["integration"], [])
