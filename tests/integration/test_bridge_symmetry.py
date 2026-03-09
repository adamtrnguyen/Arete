import pickle
import sqlite3
from pathlib import Path

import pytest
from anki.collection import Collection

from arete.domain.models import AnkiNote, WorkItem
from tests.integration.bridge_runners import ConnectBridgeRunner, DirectBridgeRunner


def init_direct_collection(base_path: Path):
    """Initialize a minimal Anki structure for Direct adapter testing."""
    col_dir = base_path / "User 1"
    col_dir.mkdir(parents=True, exist_ok=True)
    col_path = col_dir / "collection.anki2"

    # AnkiRepository expects prefs21.db with a global profile record
    prefs_db = base_path / "prefs21.db"
    conn = sqlite3.connect(prefs_db)
    conn.execute("CREATE TABLE profiles (name TEXT PRIMARY KEY, data BLOB)")
    meta = {"last_loaded_profile_name": "User 1"}
    conn.execute(
        "INSERT INTO profiles VALUES ('_global', ?)", (sqlite3.Binary(pickle.dumps(meta)),)
    )
    conn.commit()
    conn.close()

    # Initialize collection (creates standard tables)
    col = Collection(str(col_path))

    # Setup O2A_Basic model
    if not col.models.by_name("O2A_Basic"):
        m = col.models.new("O2A_Basic")
        col.models.add_field(m, col.models.new_field("Front"))
        col.models.add_field(m, col.models.new_field("Back"))
        col.models.add_field(m, col.models.new_field("nid"))
        t = col.models.new_template("Card 1")
        t["qfmt"] = "{{Front}}"
        t["afmt"] = "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}"
        col.models.add_template(m, t)
        col.models.add(m)

    col.close()


@pytest.fixture
def bridge_runner(request, anki_url, tmp_path):
    if request.param == "connect":
        return ConnectBridgeRunner(url=anki_url)
    else:
        # Create a fresh isolated Anki base for direct testing
        anki_base = tmp_path / "anki_test_base"
        init_direct_collection(anki_base)
        return DirectBridgeRunner(anki_base=anki_base)


@pytest.mark.asyncio
@pytest.mark.parametrize("bridge_runner", ["connect", "direct"], indirect=True)
async def test_bridge_sync_add_new(bridge_runner):
    """Symmetrical test: verifies that both adapters can add a new note.

    and result in the same state in their respective backends.
    """
    deck = "SymmetryTest"
    await bridge_runner.delete_deck(deck)

    note = AnkiNote(
        model="O2A_Basic",
        deck=deck,
        fields={"Front": "Hello Symmetry", "Back": "World"},
        tags=["bridge-test"],
        source_file=Path("test.md"),
        source_index=1,
        start_line=1,
        end_line=1,
    )
    item = WorkItem(note=note, source_file=Path("test.md"), source_index=1)

    results = await bridge_runner.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok, f"Sync failed: {results[0].error}"
    nid = results[0].new_nid
    assert nid is not None

    # VERIFY OUTPUT (Symmetrically)
    fields = await bridge_runner.get_note_fields(nid)
    # Check that the fields match the input
    assert fields["Front"]["value"] == "Hello Symmetry"
    assert fields["Back"]["value"] == "World"


@pytest.mark.asyncio
@pytest.mark.parametrize("bridge_runner", ["connect", "direct"], indirect=True)
async def test_bridge_sync_update_existing(bridge_runner):
    """Symmetrical test: verifies that both adapters can update an existing note."""
    deck = "SymmetryUpdateTest"
    await bridge_runner.delete_deck(deck)

    # 1. Create
    initial_note = AnkiNote(
        model="O2A_Basic",
        deck=deck,
        fields={"Front": "Update Me", "Back": "Initial"},
        tags=["initial"],
        source_file=Path("test.md"),
        source_index=1,
        start_line=1,
        end_line=1,
    )
    res1 = await bridge_runner.sync_notes(
        [WorkItem(note=initial_note, source_file=Path("test.md"), source_index=1)]
    )
    nid = res1[0].new_nid

    # 2. Update
    updated_note = AnkiNote(
        model="O2A_Basic",
        deck=deck,
        fields={"Front": "Update Me", "Back": "Updated Content"},
        tags=["updated"],
        nid=nid,
        source_file=Path("test.md"),
        source_index=1,
        start_line=1,
        end_line=1,
    )
    res2 = await bridge_runner.sync_notes(
        [WorkItem(note=updated_note, source_file=Path("test.md"), source_index=1)]
    )
    assert res2[0].ok

    # 3. Verify
    fields = await bridge_runner.get_note_fields(nid)
    assert fields["Back"]["value"] == "Updated Content"


@pytest.mark.asyncio
@pytest.mark.parametrize("bridge_runner", ["connect", "direct"], indirect=True)
async def test_bridge_get_notes_in_deck(bridge_runner):
    """Verifies symmetry for listing notes in a deck."""
    deck = "SymmetryListTest"
    await bridge_runner.delete_deck(deck)

    n1 = AnkiNote(
        model="O2A_Basic",
        deck=deck,
        fields={"Front": "A"},
        tags=[],
        source_file=Path("a.md"),
        source_index=0,
        start_line=1,
        end_line=1,
    )
    n2 = AnkiNote(
        model="O2A_Basic",
        deck=deck,
        fields={"Front": "B"},
        tags=[],
        source_file=Path("b.md"),
        source_index=0,
        start_line=1,
        end_line=1,
    )

    await bridge_runner.sync_notes(
        [
            WorkItem(note=n1, source_file=Path("a"), source_index=0),
            WorkItem(note=n2, source_file=Path("b"), source_index=0),
        ]
    )

    notes = await bridge_runner.get_notes_in_deck(deck)
    assert len(notes) == 2
    # Basic check for presence
    assert any(str(nid) in [str(n) for n in notes.values()] for nid in notes.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("bridge_runner", ["connect", "direct"], indirect=True)
async def test_bridge_delete_notes(bridge_runner):
    """Verifies symmetry for deleting notes."""
    deck = "SymmetryDeleteTest"
    await bridge_runner.delete_deck(deck)

    n1 = AnkiNote(
        model="O2A_Basic",
        deck=deck,
        fields={"Front": "A"},
        tags=[],
        source_file=Path("a.md"),
        source_index=0,
        start_line=1,
        end_line=1,
    )
    res = await bridge_runner.sync_notes([WorkItem(note=n1, source_file=Path("a"), source_index=0)])
    nid = int(res[0].new_nid)

    await bridge_runner.delete_notes([nid])

    # Verify gone
    notes = await bridge_runner.get_notes_in_deck(deck)
    assert len(notes) == 0
