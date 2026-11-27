import httpx


def test_invalid_model(tmp_path, anki_url, setup_anki, run_arete, test_deck):
    """Card references non-existent model."""
    md_file = tmp_path / "badmodel.md"
    md_file.write_text(
        f"""---
deck: {test_deck}
arete: true
cards:
  - model: NonExistentModelXYZ
    Front: Should Fail
    Back: ...
---
""",
        encoding="utf-8",
    )

    res = run_arete(tmp_path, anki_url)

    # Should probably log an error like "Model 'NonExistentModelXYZ' not found"
    # And not crash the whole process (if possible).
    # Currently AnkiConnect might return error, arete should catch it.

    assert "Model name not found" in res.stderr or "not found" in res.stdout or res.returncode != 0

    # Verify not added
    resp = httpx.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Should Fail"'},
        },
    )
    assert len(resp.json()["result"]) == 0
