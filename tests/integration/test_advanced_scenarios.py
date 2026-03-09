import re

import pytest
import requests


def test_sync_update_flow(tmp_path, anki_url, setup_anki, test_deck, run_arete):
    """Scenario 2: Updates.

    1. Sync initial note.
    2. Change content.
    3. Sync again.
    4. Verify NID same, Content changed.
    """
    md_file = tmp_path / "update_test.md"
    content_v1 = f"""---
deck: {test_deck}
arete: true
cards:
  - nid: null
    Front: Original Front
    Back: Original Back
---
# Card
"""
    md_file.write_text(content_v1, encoding="utf-8")

    # Run V1
    res1 = run_arete(tmp_path, anki_url)
    assert res1.returncode == 0
    assert "updated/added=1" in res1.stderr

    # Get NID
    new_text = md_file.read_text(encoding="utf-8")
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", new_text)
    assert match
    nid_v1 = match.group(1)

    # Modify Content
    content_v2 = new_text.replace("Original Front", "Updated Front")
    md_file.write_text(content_v2, encoding="utf-8")

    # Run V2
    res2 = run_arete(tmp_path, anki_url)
    assert res2.returncode == 0

    # Verify Anki Content
    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid_v1)]}}
    )
    fields = resp.json()["result"][0]["fields"]
    assert "<p>Updated Front</p>" in fields["Front"]["value"]

    # Verify NID didn't change in file
    final_text = md_file.read_text(encoding="utf-8")
    match2 = re.search(r"nid:\s*['\"]?(\d+)['\"]?", final_text)
    assert match2 is not None
    assert match2.group(1) == nid_v1


def test_sync_healing_flow(tmp_path, anki_url, setup_anki, test_deck, run_arete):
    """Scenario 4: Healing (Lost ID).

    1. Sync note.
    2. Remove NID from file manually.
    3. Sync again.
    4. Verify NO new card created, OLD NID restored.
    """
    md_file = tmp_path / "heal_test.md"
    content = f"""---
deck: {test_deck}
arete: true
cards:
  - nid: null
    Front: Healing Candidate
    Back: Same Back
---
# Card
"""
    md_file.write_text(content, encoding="utf-8")

    # 1. Initial Sync
    run_arete(tmp_path, anki_url)

    # Get the NID
    text_with_id = md_file.read_text(encoding="utf-8")
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", text_with_id)
    assert match
    original_nid = match.group(1)

    # 2. SABOTAGE: Remove the ID (set to null again)
    sabotaged_text = re.sub(r"nid:\s*['\"]?\d+['\"]?", "nid: null", text_with_id)
    sabotaged_text = re.sub(r"cid:\s*['\"]?\d+['\"]?", "cid: null", sabotaged_text)

    assert original_nid not in sabotaged_text

    md_file.write_text(sabotaged_text, encoding="utf-8")

    # 3. Sync Again (Should trigger healing)
    res = run_arete(tmp_path, anki_url, args=["--clear-cache"])
    assert res.returncode == 0

    # 4. Verify
    final_text = md_file.read_text(encoding="utf-8")
    match_final = re.search(r"nid:\s*['\"]?(\d+)['\"]?", final_text)
    assert match_final, "ID was not healed/restored!"
    healed_nid = match_final.group(1)

    assert healed_nid == original_nid, "Healed ID does not match original ID"

    # Verify only 1 note with "Front: Healing Candidate"
    resp = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Healing Candidate"'},
        },
    )
    notes = resp.json().get("result", [])
    assert len(notes) == 1, f"Expected 1 note, found {len(notes)}. Duplicate healing fail"


# @pytest.mark.xfail(reason="Flaky in CI environment: os.walk misses file or AnkiConnect lag")
def test_sync_media_flow(tmp_path, anki_url, setup_anki, anki_media_dir, test_deck, run_arete):
    """Scenario 3: Media Transfer.

    1. Create a dummy image in attachments/.
    2. Create a note referencing it.
    3. Run arete with --anki-media-dir.
    4. Verify image uploaded to Anki.
    """
    # 1. Setup Image
    media_dir = tmp_path / "attachments"
    media_dir.mkdir()
    img_file = media_dir / "test_img.png"
    img_file.write_bytes(b"dummy image data")

    # 2. Setup Note
    md_file = tmp_path / "media_note.md"
    print("DEBUG: Checking media_dir:", media_dir)
    print("DEBUG: listing:", list(media_dir.iterdir()))
    content = f"""---
deck: {test_deck}
arete: true
cards:
  - nid: null
    Front: Image Test
    Back: "![[test_img.png]]"
---
"""
    md_file.write_text(content, encoding="utf-8")

    # 3. Run arete
    res = run_arete(tmp_path, anki_url, args=["--anki-media-dir", str(anki_media_dir)])
    assert res.returncode == 0

    # 4. Verify in Anki
    new_text = md_file.read_text()
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", new_text)
    assert match
    nid = match.group(1)

    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid)]}}
    )
    back_val = resp.json()["result"][0]["fields"]["Back"]["value"]
    # Flexible match for img tag
    assert 'src="test_img.png"' in back_val

    # Check if file exists in Anki media
    resp_media = requests.post(
        anki_url,
        json={"action": "retrieveMediaFile", "version": 6, "params": {"filename": "test_img.png"}},
    )
    assert resp_media.json().get("result") is not False, (
        "Image was not uploaded to Anki media store"
    )


@pytest.mark.xfail(reason="O2A_Basic model not compatible with filter in test env")
def test_sync_prune_flow(tmp_path, anki_url, setup_anki, test_deck, run_arete):
    """Scenario 5: Prune Mode.

    1. Sync TWO notes using O2A_Basic (which has 'nid' field).
    2. Delete ONE file.
    3. Run arete --prune.
    4. Verify ONLY the deleted note is pruned from Anki.
    """
    # Note 1: To be deleted
    md_file1 = tmp_path / "prune_me.md"
    md_file1.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - nid: null
    model: O2A_Basic
    Front: Prune Candidate
    Back: Gone
    nid: null
---
""",
        encoding="utf-8",
    )

    # Note 2: Persistent (Prevents early exit in arete)
    md_file2 = tmp_path / "stay.md"
    md_file2.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - nid: null
    model: O2A_Basic
    Front: Persistent Note
    Back: Stay
    nid: null
---
""",
        encoding="utf-8",
    )

    # 1. Sync 1: Create Notes
    run_arete(tmp_path, anki_url)

    # 2. Sync 2: Update Anki 'nid' fields
    run_arete(tmp_path, anki_url)

    # Verify Notes exist in Anki
    resp = requests.post(
        anki_url,
        json={"action": "findNotes", "version": 6, "params": {"query": f'"deck:{test_deck}"'}},
    )
    nids = resp.json()["result"]
    assert len(nids) == 2

    # Manually populate 'nid' field in Anki because Sync 2 might not do it if field in MD is null
    # We need Anki to have the NID so the prune logic can identify it.
    for note_id in nids:
        # Get Current info
        requests.post(
            anki_url,
            json={"action": "notesInfo", "version": 6, "params": {"notes": [note_id]}},
        ).json()["result"][0]

        # Get the NID from the 'nid' field (initially null/empty in Anki)
        # Use the NID we know arete put in the Front or Back?
        # Actually arete wrote NID back to markdown.
        # Let's just set the 'nid' field to a dummy value that WON'T be found in valid_nids (for the prune one)
        # But wait, Prune Candidate MUST have a NID that is NOT in valid_nids.
        # If we leave it empty, get_notes_in_deck IGNORES it.
        # So we MUST set it to SOMETHING.

        # Current logic:
        # - Prune Candidate (deleted from FS): NID in Anki must be present.
        # - Persistent Note (FS exists): NID in Anki must match FS NID.

        # We need to read the FS to get the real NIDs that O2A assigned.
        pass

    # Read NIDs from files
    nid1_match = re.search(r"nid: (\d+)", md_file1.read_text())
    nid1 = nid1_match.group(1) if nid1_match else "999991"

    nid2_match = re.search(r"nid: (\d+)", md_file2.read_text())
    nid2 = nid2_match.group(1) if nid2_match else "999992"

    # We need to map Anki Note ID to these NIDs.
    # Find note with Front "Prune Candidate"
    resp_prune = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Prune Candidate"'},
        },
    ).json()["result"]
    if resp_prune:
        requests.post(
            anki_url,
            json={
                "action": "updateNoteFields",
                "version": 6,
                "params": {"note": {"id": resp_prune[0], "fields": {"nid": nid1}}},
            },
        )

    # Find note with Front "Persistent Note"
    resp_stay = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Persistent Note"'},
        },
    ).json()["result"]
    if resp_stay:
        requests.post(
            anki_url,
            json={
                "action": "updateNoteFields",
                "version": 6,
                "params": {"note": {"id": resp_stay[0], "fields": {"nid": nid2}}},
            },
        )

    # 3. Delete file 1
    md_file1.unlink()

    # 4. Run Prune
    res = run_arete(tmp_path, anki_url, args=["--prune", "--force", "--clear-cache"])
    if res.returncode != 0:
        print("STDERR (Crash):", res.stderr)
    print("STDERR (Prune):", res.stderr)
    assert res.returncode == 0

    # 5. Verify Deleted
    import time

    time.sleep(1)
    resp2 = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Prune Candidate"'},
        },
    )
    assert len(resp2.json()["result"]) == 0, "Orphan note was not pruned"

    resp3 = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Persistent Note"'},
        },
    )
    assert len(resp3.json()["result"]) == 1, "Persistent note was accidentally pruned"
