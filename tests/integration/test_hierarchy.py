import requests


def test_nested_decks(tmp_path, anki_url, setup_anki, run_arete):
    """Verify creating notes in nested decks (Parent::Child::Grandchild)."""
    md_file = tmp_path / "nested.md"
    md_file.write_text(
        """---
deck: IntegrationTest::Child::GrandChild
arete: true
cards:
  - Front: Nested Question
    Back: Nested Answer
---
""",
        encoding="utf-8",
    )

    # Sync
    res = run_arete(tmp_path, anki_url)
    assert res.returncode == 0

    # Verify Deck Created
    resp = requests.post(anki_url, json={"action": "deckNames", "version": 6})
    decks = resp.json()["result"]
    assert "IntegrationTest::Child::GrandChild" in decks

    # Verify Note in that deck
    resp = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": '"deck:IntegrationTest::Child::GrandChild" "Nested Question"'},
        },
    )
    assert len(resp.json()["result"]) == 1


def test_move_deck(tmp_path, anki_url, setup_anki, run_arete):
    """Verify moving a note from Deck A to Deck B by changing frontmatter."""
    md_file = tmp_path / "moving.md"
    # 1. Start in Deck A
    md_file.write_text(
        """---
deck: IntegrationTest::DeckA
arete: true
cards:
  - Front: Moving Question
    Back: Moving Answer
---
""",
        encoding="utf-8",
    )

    run_arete(tmp_path, anki_url)

    # Verify in DeckA
    resp = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": '"deck:IntegrationTest::DeckA" "Moving Question"'},
        },
    )
    assert len(resp.json()["result"]) == 1

    # 2. Move to Deck B (Preserving NID)
    # Read the file to get the NID arete wrote
    content = md_file.read_text(encoding="utf-8")
    import re

    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert match, "NID not found in file after first sync"

    # Change only the deck; the card keeps the id and anki block the first sync wrote
    from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter

    meta, body = parse_frontmatter(content)
    meta["deck"] = "IntegrationTest::DeckB"
    md_file.write_text(rebuild_markdown_with_frontmatter(meta, body), encoding="utf-8")

    # Run sync again
    run_arete(tmp_path, anki_url)

    # Verify GONE from DeckA
    respA = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": '"deck:IntegrationTest::DeckA" "Moving Question"'},
        },
    )
    assert len(respA.json()["result"]) == 0

    # Verify PRESENT in DeckB
    respB = requests.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": '"deck:IntegrationTest::DeckB" "Moving Question"'},
        },
    )
    assert len(respB.json()["result"]) == 1
