import re

import requests


def test_nid_cid_writeback(tmp_path, anki_url, setup_anki, run_arete, test_deck):
    """Verify that NID and CID are written back to the markdown file after creation.

    This ensures that the Anki bridge (AnkiConnect in this env) correctly returns
    ID information and the VaultService persists it.
    """
    md_file = tmp_path / "persistence.md"
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - Front: Persistence Test
    Back: Persistence Content
---
""",
        encoding="utf-8",
    )

    # 1. Sync (Create)
    res = run_arete(tmp_path, anki_url)
    assert res.returncode == 0
    assert "updated/added=1" in res.stdout

    # 2. Check File content
    content = md_file.read_text(encoding="utf-8")

    # Check NID
    nid_match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert nid_match, "NID NOT written back to file!"
    nid = nid_match.group(1)

    # Check CID
    # CID is usually written if we fetched it.
    cid_match = re.search(r"cid:\s*['\"]?(\d+)['\"]?", content)
    assert cid_match, "CID NOT written back to file!"
    cid = cid_match.group(1)

    # 3. Verify in Anki
    info = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid)]}}
    ).json()["result"][0]

    assert str(info["noteId"]) == nid
    assert str(info["cards"][0]) == cid
