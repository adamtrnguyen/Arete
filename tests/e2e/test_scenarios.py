import shutil
from pathlib import Path

import pytest

from tests.e2e.runners import CliRunner, ServerRunner


@pytest.fixture(params=["cli", "server"])
def runner(request):
    if request.param == "cli":
        return CliRunner()
    else:
        return ServerRunner()


@pytest.fixture
def vault_scenarios(tmp_path):
    """Return a factory that populates tmp_path with a specific scenario."""
    fixtures_root = Path("tests/fixtures/vault_scenarios").resolve()

    def _setup(scenario_name):
        source = fixtures_root / scenario_name
        dest = tmp_path / scenario_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, dirs_exist_ok=True)
        return dest

    return _setup


def test_basic_sync(runner, vault_scenarios, anki_url, setup_anki):
    vault = vault_scenarios("basic")

    # Run Sync
    runner.sync_vault(vault, anki_url, clear_cache=True)
    logs = runner.get_log_output()

    # Verify Logs
    # Note: Server runner synthesizes a simplified log string
    assert "updated/added=" in logs
    # CLI logs: generated=4 updated/added=4 (from 4 files in basic)
    # Server logs: generated=4 updated/added=4

    # Verify Content in Vault (NID writeback)
    # Basic scenarios has basic_card, healing, prune_me, stay.
    assert (vault / "basic_card.md").read_text().count("nid:") >= 1


def test_rich_content(runner, vault_scenarios, anki_url, setup_anki):
    vault = vault_scenarios("rich_text")

    runner.sync_vault(vault, anki_url, clear_cache=True)

    # Verify Math
    # Doing verification via finding NID in file, then querying Anki
    math_file = vault / "math.md"
    content = math_file.read_text()
    import re

    nid = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content).group(1)

    import requests

    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid)]}}
    )
    fields = resp.json()["result"][0]["fields"]

    # Check for MathJax signature in HTML
    # Arete converts $...$ to \( ... \) or keeps as is depending on config?
    # Usually it converts to Anki format: \( ... \)
    # But let's just check it contains something math-like
    assert "E=mc^2" in fields["Front"]["value"]

    # Verify Callout
    callout_file = vault / "callout.md"
    nid_c = re.search(r"nid:\s*['\"]?(\d+)['\"]?", callout_file.read_text()).group(1)
    resp_c = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid_c)]}}
    )
    # Callouts usually become <blockquote> or special divs
    assert "This is a callout" in resp_c.json()["result"][0]["fields"]["Front"]["value"]


def test_stats_retrieval(runner, vault_scenarios, anki_url):
    # This tests the 'test_card_management' / stats requirement
    # We first sync some cards
    vault = vault_scenarios("basic")
    runner.sync_vault(vault, anki_url)

    # Get a NID
    import re

    nid = re.search(r"nid:\s*['\"]?(\d+)['\"]?", (vault / "basic_card.md").read_text()).group(1)

    # Now use Server API for stats (regardless of runner, we want to test Server Stats endpoint)
    # But if verify 'runner' can do it?
    # The requirement is to test functionality. The stats endpoint IS server functionality.

    from fastapi.testclient import TestClient

    from arete.interface.http_server import app

    client = TestClient(app)

    resp = client.post(
        "/anki/stats",
        json={"nids": [int(nid)], "anki_connect_url": anki_url, "backend": "ankiconnect"},
    )

    assert resp.status_code == 200
    stats = resp.json()
    # stats is a list of CardStats objects
    # Note: s["card_id"] is the CID, s["note_id"] is the NID.
    found = next((s for s in stats if str(s["note_id"]) == str(nid)), None)
    assert found, f"Stats for {nid} not found in {stats}"
    assert "interval" in found
    assert "due" in found


def test_link_healing(runner, vault_scenarios, anki_url, setup_anki):
    """Test that if a local file loses its NID but the card exists in Anki,.

    Arete 'heals' the link by finding the existing card and updating the file.
    """
    vault = vault_scenarios("basic")
    healing_file = vault / "healing.md"

    # Inject unique content to ensure no collisions with other runs
    import uuid

    unique_text = f"Healing Candidate {uuid.uuid4()}"
    content = healing_file.read_text()
    import re

    content = re.sub(r"Front: .*", f"Front: {unique_text}", content)
    healing_file.write_text(content)

    # 1. Initial Sync
    runner.sync_vault(vault, anki_url, force=True)

    # Get the NID
    content = healing_file.read_text()
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert match, "Initial sync failed to write NID"
    original_nid = match.group(1)

    # 2. Sabotage: Remove NID from file
    # We replace 'nid: <digits>' with 'nid: null' to keep YAML structure valid
    import re

    new_content = re.sub(r"nid:\s*\d+", "nid: null", content)
    healing_file.write_text(new_content)

    # 3. Sync Again (should fail to create -> detect duplicate -> heal)
    runner.sync_vault(vault, anki_url, force=True)

    # 4. Verify File has the SAME NID back
    new_content = healing_file.read_text()
    assert (
        f"nid: {original_nid}" in new_content
        or f"nid: '{original_nid}'" in new_content
        or f'nid: "{original_nid}"' in new_content
    )


def test_prune_flow(runner, vault_scenarios, anki_url, setup_anki):
    """Test that deleting a file and syncing with --prune removes the card from Anki."""
    vault = vault_scenarios("basic")
    prune_file = vault / "prune_me.md"

    # 1. Initial Sync
    runner.sync_vault(vault, anki_url, force=True)

    # Verify card exists
    content = prune_file.read_text()
    import re

    nid = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content).group(1)

    import requests

    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid)]}}
    )
    assert resp.json()["result"][0].get("fields"), "Card should exist"

    # 2. Delete file
    prune_file.unlink()

    # 3. Sync with prune
    runner.sync_vault(vault, anki_url, prune=True, force=True)

    # 4. Verify card is gone
    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid)]}}
    )
    result = resp.json()["result"]
    assert result == [{}] or result == [None], f"Card {nid} should be deleted, got {result}"
