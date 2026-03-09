import subprocess
import sys


def test_basic_sync_flow(tmp_path, anki_url, setup_anki, test_deck):
    """Scenario 1: The "Hello World" Sync.

    1. Create a markdown file with a Basic card.
    2. Run arete.
    3. Verify Anki has the card.
    4. Verify markdown has the NID.
    """
    # 1. Setup Source
    md_file = tmp_path / "hello.md"
    content = f"""---
deck: {test_deck}
arete: true
cards:
  - nid: null
    Front: Hello Integration
    Back: World
---
# Card 1
"""
    md_file.write_text(content, encoding="utf-8")

    # 2. Run arete (as a subprocess to test CLI entry point)
    # We point it to our temp vault and the docker anki
    cmd = [
        sys.executable,
        "-m",
        "arete",
        "-v",
        "sync",  # Add subcommand
        str(tmp_path),  # Vault root
        "--anki-connect-url",
        anki_url,
    ]

    print(f"DEBUG: Running command: {cmd}")
    proc = subprocess.run(cmd, capture_output=True, text=True)

    assert proc.returncode == 0, f"arete failed: {proc.stderr}"
    # Logs are written to stderr by default
    assert "updated/added=1" in proc.stderr, f"Sync failed: {proc.stderr}"

    # 3. Verify Markdown Updated (NID writeback)
    new_content = md_file.read_text(encoding="utf-8")
    assert "nid: " in new_content, "NID was not written back to markdown"
    assert "cid: " in new_content, "CID was not written back to markdown"

    # Extract NID to check Anki
    import re

    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", new_content)

    assert match, f"Could not find NID in updated file. Content:\n{new_content}"

    nid = int(match.group(1))

    # 4. Verify Anki (via API)
    import requests

    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    )
    result = resp.json().get("result")

    assert result, "Anki returned no result for the NID"
    note_info = result[0]

    assert "<p>Hello Integration</p>" in note_info["fields"]["Front"]["value"]
    assert "<p>World</p>" in note_info["fields"]["Back"]["value"]
