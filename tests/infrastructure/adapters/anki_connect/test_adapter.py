"""Consolidated tests for AnkiConnectAdapter.

Covers: sync_notes, ensure_deck, ensure_model, is_responsive, _invoke,
        get_model_names, get_model_styling, get_model_templates, gui_browse,
        get_card_stats, suspend_cards, get_deck_names, get_notes_in_deck,
        delete_notes, delete_decks, get_due_cards, map_nids_to_arete_ids,
        get_card_ids_for_arete_ids, create_topo_deck, WSL detection, curl bridge,
        source field, env override.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import httpx
import pytest
import respx
from httpx import Response

from arete.domain.models import AnkiNote, WorkItem
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
        end_line=10,
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
        end_line=10,
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


def test_env_host_override():
    with patch.dict(os.environ, {"ANKI_CONNECT_HOST": "1.2.3.4"}):
        ac = AnkiConnectAdapter()
        assert ac.url == "http://1.2.3.4:8765"


# ---------------------------------------------------------------------------
# ensure_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_deck_new():
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        res = await adapter.ensure_deck("New Deck")
        assert res is True
        mock_invoke.assert_called_with("createDeck", deck="New Deck")
        assert "New Deck" in adapter._known_decks


@pytest.mark.asyncio
async def test_ensure_deck_cached():
    adapter = AnkiConnectAdapter()
    adapter._known_decks.add("Cached Deck")
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        res = await adapter.ensure_deck("Cached Deck")
        assert res is True
        mock_invoke.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_ensure_deck_failure(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Something bad"})
    )
    result = await adapter.ensure_deck("NewDeck")
    assert result is False


# ---------------------------------------------------------------------------
# ensure_model_has_source_field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_model_has_source_field_existing():
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = ["Front", "Back", "_obsidian_source"]

        res = await adapter.ensure_model_has_source_field("Basic")
        assert res is True
        assert mock_invoke.call_count == 1
        assert mock_invoke.call_args[0][0] == "modelFieldNames"


@pytest.mark.asyncio
async def test_ensure_model_has_source_field_add():
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = [
            ["Front", "Back"],  # modelFieldNames
            None,  # modelFieldAdd result
        ]

        res = await adapter.ensure_model_has_source_field("Basic")
        assert res is True
        assert mock_invoke.call_count == 2
        assert mock_invoke.call_args_list[1][0][0] == "modelFieldAdd"


@pytest.mark.asyncio
@respx.mock
async def test_ensure_model_has_source_field_adds_missing_respx(adapter):
    """Test with respx: missing field is added, and result is cached."""
    respx.post("http://mock-anki:8765").mock(
        side_effect=[
            Response(200, json={"result": ["Front", "Back"], "error": None}),
            Response(200, json={"result": None, "error": None}),
        ]
    )

    success = await adapter.ensure_model_has_source_field("Basic")
    assert success is True
    assert len(respx.calls) == 2

    # Second call uses cache
    success_cache = await adapter.ensure_model_has_source_field("Basic")
    assert success_cache is True
    assert len(respx.calls) == 2


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
    """When _invoke raises, model methods return empty defaults."""
    adapter = AnkiConnectAdapter()
    with patch.object(adapter, "_invoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = Exception("Fail")
        res = await getattr(adapter, method)("Basic")
        assert res == expected


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
    """When AnkiConnect returns an error, model methods return empty defaults."""
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Model not found"})
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
    respx.post("http://localhost:8765").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    with pytest.raises(Exception) as excinfo:
        await adapter_localhost.get_deck_names()
    assert "Connection refused" in str(excinfo.value)


# ---------------------------------------------------------------------------
# sync_notes — create new
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_create_new(adapter, sample_note):
    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "addNote":
            return Response(200, json={"result": 123456, "error": None})
        if action == "notesInfo":
            return Response(
                200, json={"result": [{"noteId": 123456, "cards": [23456]}], "error": None}
            )
        return Response(200, json={"result": None, "error": None})

    route = respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    item = WorkItem(note=sample_note, source_file=Path("test.md"), source_index=1)

    results = await adapter.sync_notes([item])

    assert len(results) == 1
    res = results[0]
    assert res.ok
    assert res.new_nid == "123456"

    assert route.called
    add_note_call = None
    for call in route.calls:
        payload = json.loads(call.request.content)
        if payload.get("action") == "addNote":
            add_note_call = payload
            break

    assert add_note_call is not None
    data = add_note_call

    # Adapter is a passthrough — raw field content is sent as-is
    assert "**bold**" in data["params"]["note"]["fields"]["Front"]
    assert r"\(\frac{1}{2}\)" in data["params"]["note"]["fields"]["Front"]


# ---------------------------------------------------------------------------
# sync_notes — update existing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_update_existing(adapter, sample_note):
    sample_note.nid = "999"

    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "notesInfo":
            return Response(200, json={"result": [{"noteId": 999}], "error": None})
        elif action in ("updateNoteFields", "createDeck", "changeDeck"):
            return Response(200, json={"result": None, "error": None})
        return Response(200, json={"result": None, "error": "Unknown action"})

    respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    item = WorkItem(note=sample_note, source_file=Path("test.md"), source_index=1)
    results = await adapter.sync_notes([item])

    assert len(results) == 1
    res = results[0]
    assert res.ok
    assert res.new_nid == "999"


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_update_existing_with_tags_and_move(adapter_localhost):
    """Full update path: fields, tags add/remove, deck move."""
    with (
        patch.object(adapter_localhost, "ensure_deck", return_value=True),
        patch.object(adapter_localhost, "ensure_model_has_source_field", return_value=True),
    ):
        respx.post("http://localhost:8765").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "result": [{"noteId": 123, "tags": ["old"], "cards": [456]}],
                        "error": None,
                    },
                ),
                httpx.Response(200, json={"result": None, "error": None}),  # updateNoteFields
                httpx.Response(200, json={"result": None, "error": None}),  # addTags
                httpx.Response(200, json={"result": None, "error": None}),  # removeTags
                httpx.Response(
                    200, json={"result": [{"cards": [456]}], "error": None}
                ),  # cards move check
                httpx.Response(200, json={"result": None, "error": None}),  # changeDeck
            ]
        )

        note = AnkiNote(
            model="Basic",
            deck="NewDeck",
            fields={"Front": "val"},
            tags=["new"],
            start_line=1,
            end_line=2,
            source_file=Path("test.md"),
            source_index=0,
            nid="123",
        )
        item = WorkItem(note=note, source_file=Path("test.md"), source_index=0)

        res = await adapter_localhost.sync_notes([item])
        assert res[0].ok is True
        assert res[0].new_nid == "123"


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_notes_info_missing_cards(adapter_localhost):
    """Update path when notesInfo has no cards key for deck move."""
    with (
        patch.object(adapter_localhost, "ensure_deck", return_value=True),
        patch.object(adapter_localhost, "ensure_model_has_source_field", return_value=True),
    ):
        respx.post("http://localhost:8765").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"result": [{"noteId": 123, "tags": [], "cards": []}], "error": None},
                ),
                httpx.Response(200, json={"result": None, "error": None}),  # updateNoteFields
                httpx.Response(
                    200, json={"result": [{"something_else": 1}], "error": None}
                ),  # cards move check
            ]
        )

        note = AnkiNote(
            model="Basic",
            deck="NewDeck",
            fields={"Front": "val"},
            tags=[],
            start_line=1,
            end_line=2,
            source_file=Path("test.md"),
            source_index=0,
            nid="123",
        )
        item = WorkItem(note=note, source_file=Path("test.md"), source_index=0)

        res = await adapter_localhost.sync_notes([item])
        assert res[0].ok is True


# ---------------------------------------------------------------------------
# sync_notes — nid not found (re-create)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_nid_not_found(adapter, sample_note):
    sample_note.nid = "888"

    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "createDeck":
            return Response(200, json={"result": None, "error": None})
        elif action == "notesInfo":
            return Response(200, json={"result": [{}], "error": None})
        elif action == "addNote":
            return Response(200, json={"result": 555, "error": None})
        return Response(200, json={"result": None, "error": f"Unexpected {action}"})

    respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    item = WorkItem(note=sample_note, source_file=Path("test.md"), source_index=1)
    results = await adapter.sync_notes([item])

    assert len(results) == 1
    res = results[0]
    assert res.ok
    assert res.new_nid == "555"


# ---------------------------------------------------------------------------
# sync_notes — add failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_add_fail(adapter):
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Q", "Back": "A"},
        tags=[],
        start_line=1,
        end_line=2,
        source_file=Path("test.md"),
        source_index=1,
    )
    item = WorkItem(note=note, source_file=Path("test.md"), source_index=1)

    respx.post("http://mock-anki:8765").mock(
        side_effect=[
            Response(200, json={"result": None, "error": None}),  # createDeck
            Response(
                200, json={"result": ["Front", "Back", "_obsidian_source"], "error": None}
            ),  # modelFieldNames
            Response(200, json={"result": [], "error": None}),  # findNotes
            Response(200, json={"result": None, "error": "Creation failed"}),  # addNote
        ]
    )

    updates = await adapter.sync_notes([item])
    assert len(updates) == 1
    assert updates[0].ok is False
    assert "Creation failed" in (updates[0].error or "")


# ---------------------------------------------------------------------------
# sync_notes — CID failure (addNote ok, notesInfo cards empty)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_cid_failure(adapter, simple_note):
    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "createDeck":
            return Response(200, json={"result": 1, "error": None})
        if action == "addNote":
            return Response(200, json={"result": 123456, "error": None})
        if action == "notesInfo":
            return Response(200, json={"result": [{"cards": []}], "error": None})
        return Response(200, json={"result": None, "error": None})

    respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    item = WorkItem(note=simple_note, source_file=Path("test.md"), source_index=1)
    results = await adapter.sync_notes([item])

    assert results[0].ok
    assert results[0].new_nid == "123456"
    assert results[0].new_cid is None


# ---------------------------------------------------------------------------
# sync_notes — ensure_model_has_source_field integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sync_notes_calls_ensure_model(adapter):
    note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Q", "Back": "A", "_obsidian_source": "v|p|1"},
        tags=[],
        start_line=1,
        end_line=2,
        source_file=Path("test.md"),
        source_index=1,
    )
    item = WorkItem(note=note, source_file=Path("test.md"), source_index=1)

    respx.post("http://mock-anki:8765").mock(
        side_effect=[
            Response(200, json={"result": None, "error": None}),  # createDeck
            Response(
                200, json={"result": ["Front", "Back", "_obsidian_source"], "error": None}
            ),  # modelFieldNames
            Response(200, json={"result": [], "error": None}),  # findNotes
            Response(200, json={"result": 123, "error": None}),  # addNote
            Response(
                200, json={"result": [{"noteId": 123, "cards": [456]}], "error": None}
            ),  # notesInfo
            Response(
                200, json={"result": ["Front", "Back", "nid"], "error": None}
            ),  # modelFieldNames
            Response(200, json={"result": None, "error": None}),  # updateNoteFields
        ]
    )

    results = await adapter.sync_notes([item])
    assert results[0].ok is True
    assert len(respx.calls) == 7
    assert "addNote" in respx.calls[3].request.content.decode()
    assert results[0].new_cid == "456"


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
    respx.post("http://mock-anki:8765").mock(
        side_effect=[
            Response(200, json={"result": [10, 11], "error": None}),  # findNotes
            Response(
                200,
                json={
                    "result": [
                        {"noteId": 10, "fields": {"nid": {"value": "obs-1"}}},
                        {"noteId": 11, "fields": {}},
                    ],
                    "error": None,
                },
            ),  # notesInfo
        ]
    )

    preview = await adapter.get_notes_in_deck("Math")
    assert preview["obs-1"] == 10
    assert 11 not in preview.values()


@pytest.mark.asyncio
@respx.mock
async def test_get_notes_in_deck_html_strip(adapter):
    """Verify that <p>123</p> is stripped to 123 for NID."""
    respx.post("http://mock-anki:8765").mock(
        side_effect=[
            Response(200, json={"result": [100], "error": None}),
            Response(
                200,
                json={
                    "result": [{"noteId": 100, "fields": {"nid": {"value": "<p> 999 </p>"}}}],
                    "error": None,
                },
            ),
        ]
    )

    mapping = await adapter.get_notes_in_deck("test_deck")
    assert "999" in mapping
    assert mapping["999"] == 100


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
async def test_get_due_cards_error(adapter):
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Anki error"})
    )
    nids = await adapter.get_due_cards()
    assert nids == []


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
    respx.post("http://mock-anki:8765").mock(
        return_value=Response(200, json={"result": None, "error": "Anki error"})
    )
    arete_ids = await adapter.map_nids_to_arete_ids([1])
    assert arete_ids == []


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
        return_value=httpx.Response(
            200, json={"result": [[300], [100], [200]], "error": None}
        )
    )

    result = await adapter_localhost.get_card_ids_for_arete_ids(["arete_C", "arete_A", "arete_B"])
    assert result == [300, 100, 200]


@pytest.mark.asyncio
@respx.mock
async def test_get_card_ids_deduplicates(adapter_localhost):
    respx.post("http://localhost:8765").mock(
        return_value=httpx.Response(
            200, json={"result": [[100, 200], [200, 300]], "error": None}
        )
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
        mock_which.side_effect = (
            lambda cmd: "/mnt/c/Windows/System32/curl.exe" if cmd == "curl.exe" else None
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
