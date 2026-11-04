"""The MCP and HTTP surfaces accept only paths inside the configured vault.

They also pass a prune confirmation through only when the caller gave one.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from arete.interface.http_server import app
from arete.interface.mcp_server import create_server

NOTE = "---\narete: true\ndeck: D\ncards:\n- Front: q\n  Back: a\n---\n\nThe body.\n"


@pytest.fixture
def outside(tmp_path):
    f = tmp_path / "elsewhere" / "secret.md"
    f.parent.mkdir()
    f.write_text(NOTE)
    return f


@pytest.fixture
def inside(configured_vault):
    f = configured_vault / "A note.md"
    f.write_text(NOTE)
    return f


def _text(result) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _stats(prune=None):
    stats = MagicMock(total_generated=0, total_imported=0, total_errors=0)
    stats.prune = prune
    return stats


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_note_body", "list_file_cards", "sync_file"])
async def test_mcp_file_tools_refuse_a_file_outside_the_vault(tool, outside, inside):
    with (
        patch("arete.interface.mcp_server.execute_sync", new_callable=AsyncMock) as sync,
        pytest.raises(ToolError, match="outside the configured vault"),
    ):
        await create_server().call_tool(tool, {"file_path": str(outside)})
    sync.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_reads_a_file_inside_the_vault(inside):
    server = create_server()
    assert _text(await server.call_tool("get_note_body", {"file_path": str(inside)})) == (
        "The body."
    )
    cards = json.loads(_text(await server.call_tool("list_file_cards", {"file_path": "A note.md"})))
    assert cards["card_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["sync_vault", "check_graph"])
async def test_mcp_vault_tools_refuse_a_folder_outside_the_vault(tool, outside, configured_vault):
    with (
        patch("arete.interface.mcp_server.execute_sync", new_callable=AsyncMock) as sync,
        pytest.raises(ToolError, match="outside the configured vault"),
    ):
        await create_server().call_tool(tool, {"vault_path": str(outside.parent)})
    sync.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_tools_refuse_everything_when_no_vault_is_configured(outside):
    with pytest.raises(ToolError, match="No vault is configured"):
        await create_server().call_tool("get_note_body", {"file_path": str(outside)})


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_mcp_sync_vault_passes_the_confirmation_and_the_configured_root(
    configured_vault, confirm
):
    report = MagicMock()
    report.to_dict.return_value = {"status": "unconfirmed", "note_count": 2}
    with patch(
        "arete.interface.mcp_server.execute_sync",
        new_callable=AsyncMock,
        return_value=_stats(report),
    ) as sync:
        result = await create_server().call_tool(
            "sync_vault", {"prune": True, "force": True, "confirm_prune": confirm}
        )
    config = sync.call_args.args[0]
    assert sync.call_args.kwargs == {"confirm_prune": confirm}
    assert config.vault_root == configured_vault == config.root_input
    assert config.prune is True
    assert json.loads(_text(result))["prune"]["note_count"] == 2


@pytest.mark.asyncio
async def test_mcp_tool_descriptions_say_force_does_not_confirm_a_prune():
    tools = {t.name: t for t in await create_server().list_tools()}
    vault = tools["sync_vault"].description or ""
    assert "does not confirm a prune" in vault
    assert "confirm_prune" in vault
    assert "never touched" in vault
    assert "confirm_prune" in tools["sync_vault"].input_schema["properties"]
    assert "Never prunes" in (tools["sync_file"].description or "")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

client = TestClient(app)


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("/sync", lambda o: {"vault_root": str(o.parent)}),
        ("/sync", lambda o: {"file_path": str(o)}),
        ("/graph", lambda o: {"vault_root": str(o.parent)}),
        ("/queue/build", lambda o: {"vault_root": str(o.parent)}),
    ],
)
def test_http_refuses_a_path_outside_the_vault(route, body, outside, configured_vault):
    with patch("arete.composition.orchestrator.execute_sync", new_callable=AsyncMock) as sync:
        res = client.post(route, json=body(outside))
    assert res.status_code == 400
    assert "outside the configured vault" in res.json()["detail"]
    sync.assert_not_called()


@pytest.mark.parametrize("confirm", [None, True])
def test_http_sync_passes_the_confirmation(configured_vault, confirm):
    body = {"vault_root": str(configured_vault), "prune": True, "force": True}
    if confirm is not None:
        body["confirm_prune"] = confirm
    with patch(
        "arete.composition.orchestrator.execute_sync",
        new_callable=AsyncMock,
        return_value=_stats(),
    ) as sync:
        res = client.post("/sync", json=body)
    assert res.status_code == 200
    assert sync.call_args.kwargs == {"confirm_prune": bool(confirm)}
    assert sync.call_args.args[0].root_input == configured_vault


def test_http_refuses_an_anki_base_other_than_the_configured_one(tmp_path):
    with patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock) as bridge:
        res = client.post("/anki/decks", json={"anki_base": str(tmp_path / "planted")})
        styled = client.get("/anki/models/Basic/styling", params={"anki_base": str(tmp_path)})
    assert res.status_code == 400 and styled.status_code == 400
    assert "differs from the configured anki_base" in res.json()["detail"]
    bridge.assert_not_called()


def test_http_accepts_the_configured_anki_base():
    from arete.application.config import resolve_config

    configured = resolve_config().anki_base
    mock = AsyncMock()
    mock.get_deck_names.return_value = ["D"]
    with patch("arete.composition.factory.get_anki_bridge", new_callable=AsyncMock) as bridge:
        bridge.return_value = mock
        res = client.post("/anki/decks", json={"anki_base": str(configured)})
    assert res.status_code == 200 and res.json()["decks"] == ["D"]
