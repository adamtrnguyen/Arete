import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response

from arete.application.config import AppConfig
from arete.application.sync.pipeline import _prune_orphans
from arete.application.utils.logging import RunRecorder
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


@pytest.fixture
def adapter():
    return AnkiConnectAdapter(url="http://mock:8765")


@pytest.fixture
def mock_config(tmp_path):
    # Use model_construct to bypass Pydantic's settings sources (TOML loading)
    return AppConfig.model_construct(
        root_input=tmp_path,
        vault_root=tmp_path,
        anki_media_dir=tmp_path,
        anki_base=None,
        apy_bin="apy",
        log_dir=tmp_path,
        run_apy=False,
        keep_going=True,
        no_move_deck=False,
        dry_run=False,
        workers=1,
        queue_size=10,
        verbose=1,
        show_config=False,
        prune=True,
        force=True,
        clear_cache=False,
        backend="auto",
        anki_connect_url="http://localhost:8765",
        open_logs=False,
        open_config=False,
    )


@pytest.mark.asyncio
@respx.mock
async def test_prune_protects_parents(adapter, mock_config):
    # Vault has: "Math::Algebra::Linear"
    # Anki has: "Math", "Math::Algebra", "Math::Algebra::Linear", "OrphanDeck"

    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "deckNames":
            return Response(
                200,
                json={
                    "result": ["Math", "Math::Algebra", "Math::Algebra::Linear", "OrphanDeck"],
                    "error": None,
                },
            )
        elif action == "findNotes":
            return Response(200, json={"result": [], "error": None})
        elif action == "deleteDecks":
            return Response(200, json={"result": None, "error": None})
        return Response(200, json={"result": None, "error": None})

    route = respx.post("http://mock:8765").mock(side_effect=side_effect)

    recorder = RunRecorder()
    recorder.add_inventory([{"nid": "1", "deck": "Math::Algebra::Linear"}])

    logger = MagicMock()
    await _prune_orphans(mock_config, recorder, adapter, logger)

    # Verify that deleteDecks was called ONLY for OrphanDeck
    delete_calls = [
        c for c in route.calls if json.loads(c.request.content).get("action") == "deleteDecks"
    ]
    assert len(delete_calls) > 0
    deleted = json.loads(delete_calls[0].request.content)["params"]["decks"]

    assert "OrphanDeck" in deleted
    assert "Math" not in deleted
    assert "Math::Algebra" not in deleted
    assert "Math::Algebra::Linear" not in deleted


# ---------------------------------------------------------------------------
# Unit tests (mocked bridge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_orphans_no_decks():
    """When no orphan decks exist, get_notes_in_deck is never called."""
    config = AppConfig(vault_root=Path("/vault"), root_input=Path("/vault"), prune=True)
    recorder = MagicMock()
    recorder.inventory_decks = ["Default"]
    bridge = AsyncMock()
    bridge.get_deck_names.return_value = ["Default"]
    logger = logging.getLogger("test_prune")

    await _prune_orphans(config, recorder, bridge, logger)
    bridge.get_notes_in_deck.assert_not_called()


@pytest.fixture
def prune_config():
    config = MagicMock()
    config.vault_root = Path("/v")
    config.root_input = Path("/v")
    config.force = False
    config.dry_run = False
    return config


@pytest.mark.asyncio
async def test_prune_orphans_aborted(prune_config):
    anki_bridge = MagicMock()
    anki_bridge.get_deck_names = AsyncMock(return_value=["Default", "Deck1"])
    anki_bridge.get_notes_in_deck = AsyncMock(return_value={"2": 222})

    recorder = MagicMock()
    recorder.inventory_nids = {"1"}  # "2" is orphan
    recorder.inventory_decks = {"Deck1"}

    logger = MagicMock()

    with patch("builtins.input", return_value="no"):
        await _prune_orphans(prune_config, recorder, anki_bridge, logger)
        anki_bridge.delete_notes.assert_not_called()


@pytest.mark.asyncio
async def test_prune_orphans_success_confirmed(prune_config):
    anki_bridge = MagicMock()
    anki_bridge.get_deck_names = AsyncMock(return_value=["Default", "Deck1"])
    anki_bridge.get_notes_in_deck = AsyncMock(return_value={"2": 222})
    anki_bridge.delete_notes = AsyncMock(return_value=True)
    anki_bridge.delete_decks = AsyncMock(return_value=True)

    recorder = MagicMock()
    recorder.inventory_nids = {"1"}
    recorder.inventory_decks = {"Deck1"}

    logger = MagicMock()

    with patch("builtins.input", return_value="yes"):
        await _prune_orphans(prune_config, recorder, anki_bridge, logger)
        anki_bridge.delete_notes.assert_called_once_with([222])


@pytest.mark.asyncio
async def test_prune_orphans_dry_run(prune_config):
    prune_config.dry_run = True
    anki_bridge = MagicMock()
    anki_bridge.get_deck_names = AsyncMock(return_value=["Default", "Deck1"])
    anki_bridge.get_notes_in_deck = AsyncMock(return_value={"2": 222})

    recorder = MagicMock()
    recorder.inventory_nids = {"1"}
    recorder.inventory_decks = {"Deck1"}

    logger = MagicMock()
    with patch("builtins.input", return_value="yes"):
        await _prune_orphans(prune_config, recorder, anki_bridge, logger)

    anki_bridge.delete_notes.assert_not_called()
    logger.warning.assert_any_call("[prune] DRY RUN: Destructive actions skipped.")


@pytest.mark.asyncio
async def test_prune_orphans_empty(prune_config):
    anki_bridge = MagicMock()
    anki_bridge.get_deck_names = AsyncMock(return_value=["Default", "Deck1"])
    anki_bridge.get_notes_in_deck = AsyncMock(return_value={"1": 111})

    recorder = MagicMock()
    recorder.inventory_nids = {"1"}
    recorder.inventory_decks = {"Deck1"}

    logger = MagicMock()
    await _prune_orphans(prune_config, recorder, anki_bridge, logger)
    anki_bridge.delete_notes.assert_not_called()


@pytest.mark.asyncio
async def test_prune_orphans_skipped_root_mismatch(prune_config):
    prune_config.root_input = Path("/v/sub")
    prune_config.vault_root = Path("/v")

    anki_bridge = MagicMock()
    recorder = MagicMock()
    logger = MagicMock()

    await _prune_orphans(prune_config, recorder, anki_bridge, logger)
    logger.warning.assert_called_with(
        "[prune] SKIPPED: Pruning requires running on the entire vault root to ensure safety."
    )


@pytest.mark.asyncio
async def test_prune_orphans_delete_error_handling(prune_config):
    anki_bridge = MagicMock()
    anki_bridge.get_deck_names = AsyncMock(return_value=["Default", "Deck1"])
    anki_bridge.get_notes_in_deck = AsyncMock(return_value={"2": 222})
    anki_bridge.delete_notes = AsyncMock(side_effect=Exception("API Error"))

    recorder = MagicMock()
    recorder.inventory_nids = {"1"}
    recorder.inventory_decks = {"Deck1"}

    logger = MagicMock()
    with patch("builtins.input", return_value="yes"):
        await _prune_orphans(prune_config, recorder, anki_bridge, logger)
        logger.error.assert_called_with("[prune] Failed to delete notes: API Error")
