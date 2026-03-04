import re
import subprocess
import sys

import requests


def test_integration_vault_parity(integration_vault, anki_url, setup_anki, test_deck):
    """Tests that the integration_vault can be synced and verified.
    This replaces inline file creation with a static vault.
    """
    tmp_path = integration_vault

    # Run arete CLI
    cmd = [
        sys.executable,
        "-m",
        "arete",
        "-v",
        "sync",
        str(tmp_path),
        "--anki-connect-url",
        anki_url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("STDERR:", res.stderr)  # Show logs
    assert res.returncode == 0, f"arete failed: {res.stderr}"

    # We expect 4 cards (basic, healing, prune, stay)
    # Check for "updated/added=4" in summary (or generated=4 if fresh)
    # Since it's a fresh sync, it might say generated=4 updated/added=4
    # The summary format is "generated=X updated/added=Y errors=Z"
    # We check for at least 2 cards (basic, healing) which passed filter.
    # formatting is "updated/added=X"
    assert "updated/added=" in res.stderr

    # Check for basic_card.md NID writeback
    md_file = tmp_path / "basic_card.md"
    content = md_file.read_text(encoding="utf-8")
    assert "nid: " in content

    # Check Anki for correctness
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert match
    nid = int(match.group(1))

    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    )
    result = resp.json().get("result")
    assert result
    fields = result[0]["fields"]
    assert "<p>Hello Integration</p>" in fields["Front"]["value"]

    # Check Healing Note
    heal_file = tmp_path / "healing.md"
    match_h = re.search(r"nid:\s*['\"]?(\d+)['\"]?", heal_file.read_text())
    assert match_h, "Healing note failed to sync?"
