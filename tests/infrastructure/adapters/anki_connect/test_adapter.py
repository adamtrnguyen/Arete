"""Consolidated tests for AnkiConnectAdapter.

Covers: is_responsive, _invoke, get_model_styling, get_model_templates, gui_browse,
        suspend_cards, get_deck_names, get_notes_in_deck, delete_notes, delete_decks,
        get_due_cards, map_nids_to_arete_ids, get_card_ids_for_arete_ids,
        create_topo_deck, WSL detection, curl bridge. The sync itself runs through
        NoteSync over the adapter's session: test_request_counts.py, test_healing.py,
        test_failure_paths.py, test_lost_replies.py and the contract suite.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import httpx
import pytest
import respx
from httpx import Response

from arete.domain.models import AnkiNote
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter():
    return AnkiConnectAdapter(url="http://mock-anki:8765")


@pytest.fixture
def adapter_localhost():
    return AnkiConnectAdapter(url="http://localhost:8765")


@pytest.fixture
def sample_note():
    return AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": r"**bold** and math \(\frac{1}{2}\)", "Back": "A"},
        tags=["tag1"],
        start_line=1,
        source_file=Path("test.md"),
        source_index=1,
    )


@pytest.fixture
def simple_note():
    return AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Front", "Back": "Back"},
        tags=["tag1"],
        start_line=1,
        source_file=Path("test.md"),
        source_index=1,
    )


# ---------------------------------------------------------------------------
# is_responsive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_responsive():
    adapter = AnkiConnectAdapter()
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": 6}
    mock_client.post.return_value = mock_resp

    adapter._client = mock_client
    assert await adapter.is_responsive() is True


@pytest.mark.asyncio
@respx.mock
async def test_is_responsive_failure_respx(adapter):
    respx.post("http://mock-anki:8765").mock(side_effect=Exception("Connection refused"))
    assert await adapter.is_responsive() is False


# ---------------------------------------------------------------------------
# env override
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# get_model_styling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_styling():
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = {"css": "body {}"}
        css = await adapter.get_model_styling("Basic")
        assert css == "body {}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,expected",
    [
        pytest.param("get_model_styling", "", id="styling"),
        pytest.param("get_model_templates", {}, id="templates"),
    ],
)
async def test_model_error_exception(method, expected):
    """A failed request raises: an empty answer would pass for the type's own."""
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = Exception("Fail")
        with pytest.raises(Exception, match="Fail"):
            await getattr(adapter, method)("Basic")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,expected",
    [
        pytest.param("get_model_styling", "", id="styling"),
        pytest.param("get_model_templates", {}, id="templates"),
    ],
)
@respx.mock
async def test_model_error_api(adapter, method, expected):
    """A type Anki does not hold reads as empty, as on the direct backend."""
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(
            200, json={"result": None, "error": "model was not found: NonExistent"}
        )
    )
    res = await getattr(adapter, method)("NonExistent")
    assert res == expected


# ---------------------------------------------------------------------------
# get_model_templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_templates():
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = {"Default": {"qfmt": "Q", "afmt": "A"}}
        temps = await adapter.get_model_templates("Basic")
        assert "Default" in temps


# ---------------------------------------------------------------------------
# suspend_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_cards_fail():
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = Exception("Fail")
        with pytest.raises(Exception, match="Fail"):
            await adapter.suspend_cards([123])


# ---------------------------------------------------------------------------
# _invoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_error_handling():
    adapter = AnkiConnectAdapter()
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.side_effect = Exception("Network Error")

        with pytest.raises(Exception, match="Network Error"):
            await adapter._invoke("version")


@pytest.mark.asyncio
@respx.mock
async def test_invoke_invalid_response(adapter):
    # Case 1: missing all fields (rare but possible w/ bad proxy)
    respx.post("http://mock-anki:8765").mock(return_value=Response(200, json={}))
    with pytest.raises(ValueError, match="unexpected number of fields"):
        await adapter._invoke("version")

    # Case 2: missing error
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": 1, "foo": "bar"})
    )
    with pytest.raises(ValueError, match="missing required error field"):
        await adapter._invoke("version")

    # Case 3: missing result
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"error": None, "foo": "bar"})
    )
    with pytest.raises(ValueError, match="missing required result field"):
        await adapter._invoke("version")


@pytest.mark.asyncio
async def test_invoke_windows_curl_failure():
    """Test the WSL curl execution path returning error."""
    ac = AnkiConnectAdapter()
    ac.use_windows_curl = True
    ac.url = "http://127.0.0.1:8765"

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        process_mock = MagicMock()
        process_mock.communicate = MagicMock(return_value=asyncio.Future())
        process_mock.communicate.return_value.set_result((b"{}", b"curl not found"))
        process_mock.returncode = 1

        mock_exec.return_value = process_mock

        with pytest.raises(Exception) as exc:
            await ac._invoke("version")

        assert "curl.exe failed" in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_invoke_connection_error(adapter_localhost):
    respx.post("http://localhost:8765").mock(side_effect=httpx.ConnectError("Connection refused"))
    with pytest.raises(Exception) as excinfo:
        await adapter_localhost.get_deck_names()
    assert "Connection refused" in str(excinfo.value)


# ---------------------------------------------------------------------------
# get_deck_names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_deck_names(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": ["Default", "Math"], "error": None})
    )
    decks = await adapter.get_deck_names()
    assert "Math" in decks


# ---------------------------------------------------------------------------
# get_notes_in_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_notes_in_deck(adapter):
    """Every note in the deck, keyed by its note id; no field is read."""
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": [10, 11], "error": None})
    )

    assert await adapter.get_notes_in_deck("Math") == {"10": 10, "11": 11}


@pytest.mark.asyncio
@respx.mock
async def test_get_notes_in_deck_empty(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": [], "error": None})
    )
    mapping = await adapter.get_notes_in_deck("empty_deck")
    assert mapping == {}


# ---------------------------------------------------------------------------
# delete_notes / delete_decks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_delete_notes(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": None})
    )
    assert await adapter.delete_notes([10, 11])


@pytest.mark.asyncio
@respx.mock
async def test_delete_decks(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": None})
    )
    assert await adapter.delete_decks(["Chemistry"])


# ---------------------------------------------------------------------------
# get_due_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_due_cards(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": [1, 2, 3], "error": None})
    )
    nids = await adapter.get_due_cards()
    assert nids == [1, 2, 3]

    # With deck filter
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": [1], "error": None})
    )
    nids = await adapter.get_due_cards(deck_name="Test")
    assert nids == [1]


@pytest.mark.asyncio
@respx.mock
async def test_get_due_cards_error_propagates(adapter):
    """An AnkiConnect error must surface, not become an empty (and wrong) queue."""
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Anki error"})
    )
    with pytest.raises(Exception, match="Anki error"):
        await adapter.get_due_cards()


# ---------------------------------------------------------------------------
# map_nids_to_arete_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_map_nids_to_arete_ids(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(
            200,
            json={
                "result": [
                    {"tags": ["arete_123", "other"]},
                    {"tags": ["other"]},
                    {"tags": ["arete_456"]},
                ],
                "error": None,
            },
        )
    )
    arete_ids = await adapter.map_nids_to_arete_ids([1, 2, 3])
    assert arete_ids == ["arete_123", "arete_456"]


@pytest.mark.asyncio
@respx.mock
async def test_map_nids_to_arete_ids_error(adapter):
    """A failed read raises: an empty answer would report nothing due."""
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Anki error"})
    )
    with pytest.raises(Exception, match="Anki error"):
        await adapter.map_nids_to_arete_ids([1])


# ---------------------------------------------------------------------------
# create_topo_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_topo_deck_delegates_to_plugin(adapter_localhost):
    respx.post("http://localhost:8765").mock(
        return_value=httpx.Response(200, json={"result": 42, "error": None})
    )

    result = await adapter_localhost.create_topo_deck("Arete::Queue", [100, 200, 300])
    assert result is True

    req = respx.calls.last.request
    body = json.loads(req.content)
    assert body["action"] == "createFilteredDeck"
    assert body["params"]["name"] == "Arete::Queue"
    assert body["params"]["cids"] == [100, 200, 300]
    assert body["params"]["reschedule"] is True


@pytest.mark.asyncio
@respx.mock
async def test_create_topo_deck_handles_plugin_error(adapter_localhost):
    respx.post("http://localhost:8765").mock(
        return_value=httpx.Response(
            200, json={"result": None, "error": "Deck exists and is not filtered"}
        )
    )

    result = await adapter_localhost.create_topo_deck("Queue", [100])
    assert result is False


# ---------------------------------------------------------------------------
# get_card_ids_for_arete_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_card_ids_preserves_order(adapter_localhost):
    respx.post("http://localhost:8765").mock(
        return_value=httpx.Response(200, json={"result": [[300], [100], [200]], "error": None})
    )

    result = await adapter_localhost.get_card_ids_for_arete_ids(["arete_C", "arete_A", "arete_B"])
    assert result == [300, 100, 200]


@pytest.mark.asyncio
@respx.mock
async def test_get_card_ids_deduplicates(adapter_localhost):
    respx.post("http://localhost:8765").mock(
        return_value=httpx.Response(200, json={"result": [[100, 200], [200, 300]], "error": None})
    )

    result = await adapter_localhost.get_card_ids_for_arete_ids(["arete_A", "arete_B"])
    assert result == [100, 200, 300]


# ---------------------------------------------------------------------------
# WSL detection
# ---------------------------------------------------------------------------


def test_wsl_detection_active():
    """Verify that we replace localhost with nameserver IP when on WSL."""
    mock_uname = "Linux 5.10.16.3-microsoft-standard-WSL2"
    mock_resolv = "nameserver 172.17.0.1\n"

    with patch("platform.uname") as mock_platform:
        mock_platform.return_value.release = mock_uname
        with patch("shutil.which", return_value=None):
            with patch("builtins.open", mock_open(read_data=mock_resolv)):
                adapter = AnkiConnectAdapter(url="http://localhost:8765")
            assert adapter.url == "http://172.17.0.1:8765"


def test_wsl_detection_non_wsl():
    """Verify no change if not on WSL."""
    mock_uname = "Darwin 21.6.0"

    with patch("platform.uname") as mock_platform:
        mock_platform.return_value.release = mock_uname
        with patch("builtins.open", mock_open(read_data="")) as m_open:
            adapter = AnkiConnectAdapter(url="http://localhost:8765")
            assert adapter.url == "http://localhost:8765"
            m_open.assert_not_called()


@pytest.mark.parametrize("release", ["10", "11", "2025Server"])
def test_native_windows_is_not_taken_for_wsl(release):
    """Windows ships curl.exe; only a WSL kernel release may switch to the curl bridge."""
    with (
        patch("platform.uname") as mock_platform,
        patch("shutil.which", return_value=r"C:\Windows\System32\curl.exe"),
        patch("builtins.open", mock_open(read_data="nameserver 172.17.0.1\n")) as m_open,
    ):
        mock_platform.return_value.release = release
        adapter = AnkiConnectAdapter(url="http://localhost:8765")
    assert (adapter.use_windows_curl, adapter.url) == (False, "http://localhost:8765")
    m_open.assert_not_called()


def test_wsl_detection_failed_read():
    """Verify safeguard if /etc/resolv.conf is unreadable or malformed."""
    mock_uname = "Linux 5.10.16.3-microsoft-standard-WSL2"

    with patch("platform.uname") as mock_platform:
        mock_platform.return_value.release = mock_uname
        with patch("builtins.open", side_effect=OSError("Read error")):
            adapter = AnkiConnectAdapter(url="http://localhost:8765")
            assert adapter.url in ("http://localhost:8765", "http://127.0.0.1:8765")


@pytest.mark.asyncio
async def test_wsl_detection_curl_found():
    """WSL with curl.exe found — uses curl bridge and 127.0.0.1."""
    with patch("platform.uname") as mock_uname:
        mock_uname.return_value.release = "microsoft-standard-WSL2"
        with patch("shutil.which", return_value="/mnt/c/Windows/System32/curl.exe"):
            ac = AnkiConnectAdapter(url="http://localhost:8765")
            assert ac.use_windows_curl is True
            assert ac.url == "http://127.0.0.1:8765"


# ---------------------------------------------------------------------------
# curl bridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curl_bridge_active():
    """Verify that we use curl.exe when available on WSL."""
    mock_uname = "Linux 5.10.16.3-microsoft-standard-WSL2"

    with (
        patch("platform.uname") as mock_platform,
        patch("shutil.which") as mock_which,
        patch("asyncio.create_subprocess_exec") as mock_exec,
    ):
        mock_platform.return_value.release = mock_uname
        mock_which.side_effect = lambda cmd: (
            "/mnt/c/Windows/System32/curl.exe" if cmd == "curl.exe" else None
        )

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b'{"result": 6, "error": null}', b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        adapter = AnkiConnectAdapter(url="http://localhost:8765")

        assert adapter.use_windows_curl is True
        assert adapter.url == "http://127.0.0.1:8765"

        res = await adapter._invoke("version")
        assert res == 6

        mock_exec.assert_called()
        cmd = mock_exec.call_args[0]
        assert cmd[0] == "curl.exe"
        assert cmd[4] == "http://127.0.0.1:8765"


@pytest.mark.asyncio
async def test_curl_bridge_fallback():
    """Verify fallback to standard logic if curl.exe is missing."""
    mock_uname = "Linux 5.10.16.3-microsoft-standard-WSL2"

    with (
        patch("platform.uname") as mock_platform,
        patch("shutil.which") as mock_which,
        patch("builtins.open"),
    ):
        mock_platform.return_value.release = mock_uname
        mock_which.return_value = None

        adapter = AnkiConnectAdapter(url="http://localhost:8765")

        assert adapter.use_windows_curl is False
