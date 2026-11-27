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
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import httpx
import pytest
import respx
from httpx import Response

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


# ---------------------------------------------------------------------------
# is_responsive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_is_responsive(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": 6, "error": None})
    )
    assert await adapter.is_responsive() is True


@pytest.mark.asyncio
@respx.mock
async def test_is_responsive_failure_respx(adapter):
    respx.post("http://mock-anki:8765").mock(side_effect=Exception("Connection refused"))
    assert await adapter.is_responsive() is False


# ---------------------------------------------------------------------------
# get_model_styling / get_model_templates / suspend_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_model_styling(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": {"css": "body {}"}, "error": None})
    )
    assert await adapter.get_model_styling("Basic") == "body {}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,expected",
    [
        pytest.param("get_model_styling", "", id="styling"),
        pytest.param("get_model_templates", {}, id="templates"),
    ],
)
@respx.mock
async def test_model_error_exception(adapter, method, expected):
    """A failed request raises: an empty answer would pass for the type's own."""
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Fail"})
    )
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


@pytest.mark.asyncio
@respx.mock
async def test_get_model_templates(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(
            200, json={"result": {"Default": {"qfmt": "Q", "afmt": "A"}}, "error": None}
        )
    )
    temps = await adapter.get_model_templates("Basic")
    assert "Default" in temps


@pytest.mark.asyncio
@respx.mock
async def test_suspend_cards_fail(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Fail"})
    )
    with pytest.raises(Exception, match="Fail"):
        await adapter.suspend_cards([123])


# ---------------------------------------------------------------------------
# _invoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_invoke_error_handling(adapter):
    respx.post("http://mock-anki:8765").mock(side_effect=RuntimeError("Network Error"))
    with pytest.raises(RuntimeError, match="Network Error"):
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
    ac = AnkiConnectAdapter(url="http://127.0.0.1:8765")
    ac.client.use_windows_curl = True

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
    aid1 = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N1"
    aid2 = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N2"
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(
            200,
            json={
                "result": [
                    {"tags": ["arete_review", aid1, "other"]},
                    {"tags": ["other", "arete_custom"]},
                    {"tags": [aid2]},
                ],
                "error": None,
            },
        )
    )
    arete_ids = await adapter.map_nids_to_arete_ids([1, 2, 3])
    assert arete_ids == [aid1, aid2]


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
    assert (adapter.client.use_windows_curl, adapter.url) == (False, "http://localhost:8765")
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
            assert ac.client.use_windows_curl is True
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

        assert adapter.client.use_windows_curl is True
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

        assert adapter.client.use_windows_curl is False


@pytest.mark.asyncio
async def test_is_responsive_uses_curl_on_wsl():
    """On WSL with curl.exe bridge, is_responsive must route through _http_request."""
    adapter = AnkiConnectAdapter(url="http://127.0.0.1:8765")
    adapter.client.use_windows_curl = True
    with patch.object(adapter.client, "_http_request", new_callable=AsyncMock) as mock_http:
        mock_http.return_value = {"result": 6, "error": None}
        assert await adapter.is_responsive() is True
        mock_http.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_responsive_initializes_client_with_limits():
    """is_responsive shares _ensure_client so httpx.Limits are applied under auto mode."""
    from arete.domain.constants import SYNC_CONCURRENCY

    adapter = AnkiConnectAdapter(url="http://127.0.0.1:8765")
    adapter.client.use_windows_curl = False
    with respx.mock:
        respx.post("http://127.0.0.1:8765").mock(
            return_value=httpx.Response(200, json={"result": 6, "error": None})
        )
        assert await adapter.is_responsive() is True
        assert adapter.client._client is not None
        assert adapter.client._client._transport._pool._max_connections == SYNC_CONCURRENCY
    await adapter.close()


@pytest.mark.asyncio
async def test_suspend_and_unsuspend_return_true_on_none_or_false_reply():
    """Add-on unsuspend returns None and suspend returns False if already suspended."""
    adapter = AnkiConnectAdapter(url="http://mock:8765")
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = None
        assert await adapter.unsuspend_cards([1]) is True
        mock_invoke.return_value = False
        assert await adapter.suspend_cards([1]) is True


@pytest.mark.asyncio
async def test_addon_outdated_propagates_for_change_note_type_and_create_filtered_deck():
    """Unsupported changeNoteType and createFilteredDeck surface AddonOutdated."""
    from arete.domain.models import AddonOutdated, NoteChange
    from arete.infrastructure.adapters.anki_connect import ConnectNoteSession, _model_info

    assert (
        _model_info("basic", {"name": "Basic", "type": 0, "fields": ["Front"], "templates": 1}).name
        == "Basic"
    )

    adapter = AnkiConnectAdapter(url="http://mock:8765")
    session = ConnectNoteSession(adapter)
    change = NoteChange(nid=1, retype=("Cloze", (0,)))
    with pytest.raises(AddonOutdated, match="changeNoteType"):
        await session._settle(change, ("changeNoteType", {}), Exception("unsupported action"))

    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = Exception("unsupported action")
        with pytest.raises(AddonOutdated, match="createFilteredDeck"):
            await adapter.create_topo_deck("Arete::Queue", [1])


@pytest.mark.asyncio
@respx.mock
async def test_get_card_ids_sends_version_6_in_multi_subactions(adapter_localhost):
    route = respx.post("http://localhost:8765").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"result": [101], "error": None}], "error": None},
        )
    )
    cids = await adapter_localhost.get_card_ids_for_arete_ids(["arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N1"])
    assert cids == [101]
    body = json.loads(route.calls[0].request.content)
    assert body["params"]["actions"][0]["version"] == 6


@pytest.mark.asyncio
async def test_curl_bridge_uses_fsS_and_max_time_flags():
    from arete.domain.constants import REQUEST_TIMEOUT

    adapter = AnkiConnectAdapter(url="http://127.0.0.1:8765")
    adapter.client.use_windows_curl = True
    proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(b'{"result": 6}', b"")))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec:
        out = await adapter.client._http_request({"action": "version", "version": 6})
    assert out == {"result": 6}
    args = list(mock_exec.await_args.args)
    assert "-fsS" in args
    assert args[args.index("--max-time") + 1] == str(REQUEST_TIMEOUT)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec:
        await adapter.client._http_request({"action": "version", "version": 6}, timeout=2.0)
    args = list(mock_exec.await_args.args)
    assert args[args.index("--max-time") + 1] == "2.0"


@pytest.mark.asyncio
async def test_curl_bridge_kills_subprocess_on_cancellation():
    adapter = AnkiConnectAdapter(url="http://127.0.0.1:8765")
    adapter.client.use_windows_curl = True

    async def stall(*_args, **_kwargs):
        await asyncio.sleep(10)

    proc = MagicMock(returncode=None)
    proc.communicate = AsyncMock(side_effect=stall)
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=-9)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                adapter.client._http_request({"action": "version", "version": 6}),
                timeout=0.01,
            )

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_guards_non_dict_reply():
    adapter = AnkiConnectAdapter(url="http://mock:8765")
    for bad in (None, "not-a-dict", [1, 2]):
        with patch.object(adapter.client, "_http_request", AsyncMock(return_value=bad)):
            with pytest.raises(ValueError, match="unexpected number of fields"):
                await adapter._invoke("version")
