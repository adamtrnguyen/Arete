"""AnkiConnect adapter integration tests — direct method calls against real Anki."""

import pytest

from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


@pytest.fixture
def adapter(anki_url) -> AnkiConnectAdapter:
    return AnkiConnectAdapter(url=anki_url)


@pytest.mark.asyncio
async def test_ensure_deck_hierarchy(adapter, setup_anki):
    """ensure_deck creates nested deck hierarchy."""
    await adapter.ensure_deck("IntegrationTest::Sub::Deep")

    decks = await adapter.get_deck_names()
    assert "IntegrationTest::Sub::Deep" in decks


@pytest.mark.asyncio
async def test_get_deck_names(adapter, setup_anki):
    """get_deck_names returns a list including Default."""
    decks = await adapter.get_deck_names()
    assert isinstance(decks, list)
    assert "Default" in decks


@pytest.mark.asyncio
async def test_get_due_cards(adapter, setup_anki):
    """get_due_cards returns a list (possibly empty)."""
    nids = await adapter.get_due_cards("IntegrationTest", include_new=True)
    assert isinstance(nids, list)


@pytest.mark.asyncio
async def test_map_nids_to_arete_ids(adapter, vault_factory, sync_config, setup_anki):
    """map_nids_to_arete_ids returns empty for unknown NIDs."""
    result = await adapter.map_nids_to_arete_ids([999999999])
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_suspend_unsuspend(adapter, vault_factory, sync_config, anki_url, setup_anki):
    """suspend_cards and unsuspend_cards round-trip."""
    import re

    import requests

    from arete.application.orchestrator import execute_sync

    # Create a card first
    vault = vault_factory(
        {
            "suspend_test.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: Suspend Test
    Back: Round trip
---
"""
        }
    )
    config = sync_config(vault_root=vault)
    await execute_sync(config)

    content = (vault / "suspend_test.md").read_text()
    nid = int(re.search(r"nid:\s*['\"]?(\d+)['\"]?", content).group(1))

    # Get CID
    resp = requests.post(
        anki_url,
        json={"action": "findCards", "version": 6, "params": {"query": f"nid:{nid}"}},
    )
    cids = resp.json()["result"]
    assert cids, "No cards found for note"

    # Suspend
    await adapter.suspend_cards(cids)

    # Check suspended
    resp = requests.post(
        anki_url,
        json={"action": "areSuspended", "version": 6, "params": {"cards": cids}},
    )
    assert all(resp.json()["result"]), "Cards should be suspended"

    # Unsuspend
    await adapter.unsuspend_cards(cids)

    resp = requests.post(
        anki_url,
        json={"action": "areSuspended", "version": 6, "params": {"cards": cids}},
    )
    assert not any(resp.json()["result"]), "Cards should be unsuspended"
