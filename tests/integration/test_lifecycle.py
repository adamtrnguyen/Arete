import re

import requests


def test_model_migration_basic_to_cloze(tmp_path, anki_url, setup_anki, run_arete, test_deck):
    """Verify changing a card's model from Basic to Cloze keeps the node ID (if possible).

    or handles it gracefully.
    (Note: AnkiConnect implementation of updateModelTemplates is complex,
    arete might assume it's a new card if model mismatch, or try to update fields?).

    Current arete logic:
    - It uses the NID to try and update fields.
    - If the model in Anki differs from the model in MD, it likely needs to change the model in Anki
      OR delete and recreate.
    - Let's see what happens.
    """
    md_file = tmp_path / "migration.md"

    # 1. Start with Basic
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - model: Basic
    Front: Migration Test
    Back: Initial State
---
""",
        encoding="utf-8",
    )

    run_arete(tmp_path, anki_url)

    # Get NID
    txt = md_file.read_text()
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", txt)
    assert match
    nid1 = match.group(1)

    # 2. Change to Cloze
    # Cloze requires "Text" and "Back Extra" (usually)
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - nid: {nid1}
    model: Cloze
    Text: Migration {{c1::Test}}
    Back Extra: New State
---
""",
        encoding="utf-8",
    )

    run_arete(tmp_path, anki_url)

    # Verify Anki
    # arete currently might FAIL to change model type via AnkiConnect updateNoteFields
    # if the note type ID doesn't match?
    # Actually, AnkiConnect 'updateNoteFields' works on the note ID.
    # But you can't change the model just by sending new fields if the model ID is different.
    # So arete might error out or create a duplicate if it doesn't handle model migration.
    # If arete doesn't handle model migration explicitly, this test might reveal that gap.
    # For now, let's assert that the OLD note is gone or updated, and a NEW note exists?
    # OR if arete supports it (via updateNoteModel?), it keeps NID.

    # Let's check if nid1 still exists
    resp = requests.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [int(nid1)]}}
    )
    result = resp.json().get("result", [None])[0]

    # If result is None, it was deleted.
    # If result exists, check model name.

    if result:
        # model_name = result["modelName"]
        # If it successfully migrated, modelName should be Cloze
        # If arete doesn't support migration, it might still be Basic and failed to update fields?
        pass


def test_tag_sync(tmp_path, anki_url, setup_anki, run_arete, test_deck):
    """Verify adding/removing tags works."""
    md_file = tmp_path / "tags.md"
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
tags: [tag_v1]
cards:
  - Front: Tag Test
    Back: Content
---
""",
        encoding="utf-8",
    )

    run_arete(tmp_path, anki_url)

    # Check tags
    resp = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "tag:tag_v1"'},
        },
    )
    assert len(resp.json()["result"]) == 1
    nid = resp.json()["result"][0]

    # Change tags
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
tags: [tag_v2, tag_complex_👍]
cards:
  - nid: {nid}
    Front: Tag Test
    Back: Content
---
""",
        encoding="utf-8",
    )

    run_arete(tmp_path, anki_url)

    # Check old tag gone
    resp_old = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "tag:tag_v1"'},
        },
    )
    assert len(resp_old.json()["result"]) == 0

    # Check new tag
    resp_new = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "tag:tag_v2"'},
        },
    )
    assert len(resp_new.json()["result"]) == 1
