"""Pipeline integration tests — call execute_sync() directly against real Anki."""

import re

import pytest
import requests

from arete.application.orchestrator import execute_sync


@pytest.fixture
def single_card_vault(vault_factory):
    """Vault with one basic card."""
    return vault_factory(
        {
            "hello.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: Hello Pipeline
    Back: World
---
# Hello
"""
        }
    )


@pytest.fixture
def multi_card_vault(vault_factory):
    """Vault with one file containing multiple cards."""
    return vault_factory(
        {
            "multi.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: Card A
    Back: Answer A
  - nid: null
    Front: Card B
    Back: Answer B
  - nid: null
    Front: Card C
    Back: Answer C
---
# Multi
"""
        }
    )


@pytest.fixture
def multi_file_vault(vault_factory):
    """Vault with multiple files in different decks."""
    return vault_factory(
        {
            "file1.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: File1 Card
    Back: File1 Answer
---
""",
            "subdir/file2.md": """\
---
deck: IntegrationTest::Sub
arete: true
cards:
  - nid: null
    Front: File2 Card
    Back: File2 Answer
---
""",
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_card(single_card_vault, sync_config, anki_url, setup_anki):
    """Single card syncs to Anki, NID written back."""
    config = sync_config(vault_root=single_card_vault)
    stats = await execute_sync(config)

    assert stats.total_imported == 1
    assert stats.total_errors == 0

    # NID writeback
    content = (single_card_vault / "hello.md").read_text()
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert match, f"NID not written back. Content:\n{content}"

    # Verify in Anki
    nid = int(match.group(1))
    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    )
    fields = resp.json()["result"][0]["fields"]
    assert "Hello Pipeline" in fields["Front"]["value"]


@pytest.mark.asyncio
async def test_sync_updates_card(single_card_vault, sync_config, anki_url, setup_anki):
    """Modify content, re-sync, verify update in Anki."""
    config = sync_config(vault_root=single_card_vault)
    await execute_sync(config)

    # Get NID
    content = (single_card_vault / "hello.md").read_text()
    nid = int(re.search(r"nid:\s*['\"]?(\d+)['\"]?", content).group(1))

    # Modify
    updated = content.replace("Hello Pipeline", "Updated Pipeline")
    (single_card_vault / "hello.md").write_text(updated)

    # Re-sync
    config2 = sync_config(vault_root=single_card_vault)
    stats2 = await execute_sync(config2)
    assert stats2.total_errors == 0

    # Verify in Anki
    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    )
    assert "Updated Pipeline" in resp.json()["result"][0]["fields"]["Front"]["value"]


@pytest.mark.asyncio
async def test_sync_multiple_cards(multi_card_vault, sync_config, anki_url, setup_anki):
    """File with 3 cards all sync successfully."""
    config = sync_config(vault_root=multi_card_vault)
    stats = await execute_sync(config)

    assert stats.total_imported == 3
    assert stats.total_errors == 0

    content = (multi_card_vault / "multi.md").read_text()
    nids = re.findall(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert len(nids) == 3, f"Expected 3 NIDs written back, got {len(nids)}"


@pytest.mark.asyncio
async def test_sync_multi_file(multi_file_vault, sync_config, anki_url, setup_anki):
    """Multiple files in different decks."""
    config = sync_config(vault_root=multi_file_vault)
    stats = await execute_sync(config)

    assert stats.total_imported == 2
    assert stats.total_errors == 0

    # Both files got NIDs
    for name in ["file1.md", "subdir/file2.md"]:
        content = (multi_file_vault / name).read_text()
        assert re.search(r"nid:\s*['\"]?(\d+)['\"]?", content), f"No NID in {name}"


@pytest.mark.asyncio
async def test_sync_idempotent(single_card_vault, sync_config, anki_url, setup_anki):
    """Second sync produces no errors and preserves the same NID."""
    config = sync_config(vault_root=single_card_vault)
    stats1 = await execute_sync(config)
    assert stats1.total_imported == 1

    content_after_first = (single_card_vault / "hello.md").read_text()
    nid1 = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content_after_first).group(1)

    # Second sync — same config (clear_cache=False this time)
    config2 = sync_config(vault_root=single_card_vault, clear_cache=False)
    stats2 = await execute_sync(config2)
    assert stats2.total_errors == 0

    # NID should be unchanged
    content_after_second = (single_card_vault / "hello.md").read_text()
    nid2 = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content_after_second).group(1)
    assert nid1 == nid2, "NID should be stable across syncs"


@pytest.mark.asyncio
async def test_sync_assigns_arete_ids(vault_factory, sync_config, anki_url, setup_anki):
    """Cards missing `id:` get ULIDs assigned."""
    vault = vault_factory(
        {
            "no_id.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: No ID Card
    Back: Should get one
---
"""
        }
    )
    config = sync_config(vault_root=vault)
    await execute_sync(config)

    content = (vault / "no_id.md").read_text()
    assert re.search(r"id:\s*arete_", content), f"Arete ID not assigned. Content:\n{content}"


@pytest.mark.asyncio
async def test_sync_with_media(vault_factory, sync_config, anki_url, anki_media_dir, setup_anki):
    """Card with image reference syncs and media is copied."""
    vault = vault_factory(
        {
            "media_card.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: Image Card
    Back: "![[test_integration.png]]"
---
""",
            "attachments/test_integration.png": "fake image bytes",
        }
    )
    config = sync_config(vault_root=vault)
    stats = await execute_sync(config)

    assert stats.total_imported == 1
    assert stats.total_errors == 0

    content = (vault / "media_card.md").read_text()
    nid = int(re.search(r"nid:\s*['\"]?(\d+)['\"]?", content).group(1))

    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    )
    back = resp.json()["result"][0]["fields"]["Back"]["value"]
    assert "test_integration.png" in back


@pytest.mark.asyncio
async def test_sync_dry_run(single_card_vault, sync_config, anki_url, setup_anki):
    """dry_run=True produces no mutations in Anki."""
    config = sync_config(vault_root=single_card_vault, dry_run=True)
    stats = await execute_sync(config)

    # Dry run should report generated but not imported
    assert stats.total_errors == 0

    # No NID written back
    content = (single_card_vault / "hello.md").read_text()
    assert not re.search(r"nid:\s*['\"]?\d+['\"]?", content), "NID should not be written in dry_run"


@pytest.mark.asyncio
async def test_sync_writeback_format(single_card_vault, sync_config, anki_url, setup_anki):
    """NID and CID are written back in the anki: YAML block."""
    config = sync_config(vault_root=single_card_vault)
    await execute_sync(config)

    content = (single_card_vault / "hello.md").read_text()
    assert re.search(r"nid:\s*['\"]?\d+['\"]?", content), "NID missing"
    assert re.search(r"cid:\s*['\"]?\d+['\"]?", content), "CID missing"


@pytest.mark.asyncio
async def test_sync_error_continues(vault_factory, sync_config, anki_url, setup_anki):
    """Bad YAML in one file doesn't stop other files from syncing."""
    vault = vault_factory(
        {
            "good.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: Good Card
    Back: Works
---
""",
            "bad.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: {{{invalid yaml
---
""",
        }
    )
    config = sync_config(vault_root=vault)
    await execute_sync(config)

    # Good card should still sync
    content = (vault / "good.md").read_text()
    assert re.search(r"nid:\s*['\"]?\d+['\"]?", content), "Good card should have synced"
