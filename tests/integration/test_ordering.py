import requests


def test_list_reordering(tmp_path, anki_url, setup_anki, run_arete, test_deck):
    """Verify that swapping card order in YAML creates stable updates (via NID).

    instead of overwriting based on index.

    1. Create A and B. Get NIDs.
    2. Swap order in YAML.
    3. Sync.
    4. Verify NIDs match original content.
    """
    md_file = tmp_path / "order.md"

    # 1. Initial
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - Front: Card A
    Back: Back A
  - Front: Card B
    Back: Back B
---
""",
        encoding="utf-8",
    )

    run_arete(tmp_path, anki_url)

    # Read assigned NIDs
    # txt = md_file.read_text()

    # We need to parse which NID belongs to which card.
    # Since arete writes NIDs back to the file, let's grab them.
    # But arete writes them into the YAML list.

    # Let's just rely on Anki content.
    resp_a = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Card A"'},
        },
    ).json()["result"]
    nid_a = resp_a[0]

    resp_b = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Card B"'},
        },
    ).json()["result"]
    nid_b = resp_b[0]

    # 2. Swap Order AND Add explicit NIDs (which arete should have done)
    # Actually, let's read the file to get the exact NIDs arete wrote so we can swap them correctly
    # to simulate user rearranging blocks *with* their existing IDs.

    # The file content will look like:
    # cards:
    #   - Front: Card A
    #     Back: Back A
    #     nid: 123
    # ...

    # lines = md_file.read_text().splitlines()
    # This is fragile parsing, but sufficient for test if we construct it well.
    # Instead, let's just constructing v2 content using the known nids from Anki
    # (since arete syncs nids to file matching anki).

    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - nid: {nid_b}
    Front: Card B
    Back: Back B Modified
  - nid: {nid_a}
    Front: Card A
    Back: Back A Modified
---
""",
        encoding="utf-8",
    )

    # 3. Sync
    run_arete(tmp_path, anki_url)

    # 4. Verify Content
    # Card A (nid_a) should now have "Back A Modified"
    info_a = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid_a]}}
    ).json()["result"][0]
    assert "Back A Modified" in info_a["fields"]["Back"]["value"]
    assert "Card A" in info_a["fields"]["Front"]["value"]

    # Card B (nid_b) should now have "Back B Modified"
    info_b = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid_b]}}
    ).json()["result"][0]
    assert "Back B Modified" in info_b["fields"]["Back"]["value"]
    assert "Card B" in info_b["fields"]["Front"]["value"]
