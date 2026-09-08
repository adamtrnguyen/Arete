import json
import sqlite3
from pathlib import Path

from arete.infrastructure.persistence.cache import ContentCache

# ... (existing tests) ...


def test_note_json_storage(tmp_path):
    """Verify storing and retrieving rendered AnkiNote JSON."""
    db_path = tmp_path / "test_notes.db"
    cache = ContentCache(db_path=db_path)

    md_file = Path("/tmp/note.md")
    card_index = 1
    content_hash = "abc_hash"
    note_json = json.dumps({"nid": "123", "deck": "Default", "fields": {"Front": "A", "Back": "B"}})

    # Initial: None
    assert cache.get_note(md_file, card_index) is None

    # Set note
    cache.set_note(md_file, card_index, content_hash, note_json)

    # Verify retrieval
    # get_note returns (hash, content_json)
    retrieved = cache.get_note(md_file, card_index)
    assert retrieved is not None
    r_hash, r_json = retrieved
    assert r_hash == content_hash
    assert r_json == note_json

    # Check actual persistence in DB
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT note_json FROM cards WHERE path=? AND idx=?", (str(md_file), card_index)
        ).fetchone()
        assert row is not None
        assert row[0] == note_json


def test_fuzzy_mtime_matching(tmp_path):
    """Verify fuzzy mtime matching (epsilon check) for file metadata."""
    db_path = tmp_path / "test_stat.db"
    cache = ContentCache(db_path=db_path)

    md_file = Path("/tmp/fuzzy.md")
    current_hash = "hash123"
    meta = {"cards": [1]}

    # Store with specific mtime
    base_mtime = 1700000000.123456
    size = 100
    cache.set_file_meta(md_file, current_hash, meta, mtime=base_mtime, size=size)

    # 1. Exact match
    assert cache.get_file_meta_by_stat(md_file, base_mtime, size) == meta

    # 2. Epsilon match (within 0.001s)
    # +0.0005s
    assert cache.get_file_meta_by_stat(md_file, base_mtime + 0.0005, size) == meta
    # -0.0005s
    assert cache.get_file_meta_by_stat(md_file, base_mtime - 0.0005, size) == meta

    # 3. Epsilon miss (outside 0.001s)
    # +0.002s
    assert cache.get_file_meta_by_stat(md_file, base_mtime + 0.002, size) is None

    # 4. Size mismatch
    assert cache.get_file_meta_by_stat(md_file, base_mtime, size + 1) is None


def test_cache_init(tmp_path):
    # Should create DB file if not exists
    db_path = tmp_path / "test.db"
    ContentCache(db_path=db_path)
    assert db_path.exists()

    # Check tables
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "cards" in tables
        assert "files" in tables


def test_card_hash_roundtrip(tmp_path):
    db_path = tmp_path / "test.db"
    cache = ContentCache(db_path=db_path)

    md_file = Path("/tmp/note.md")

    # Initial: should be None
    assert cache.get_hash(md_file, 1) is None

    # Set hash
    cache.set_note(md_file, 1, "abc123hash", "{}")

    # Verify retrieval
    assert cache.get_hash(md_file, 1) == "abc123hash"

    # Verify update
    cache.set_note(md_file, 1, "newhash", "{}")
    assert cache.get_hash(md_file, 1) == "newhash"


def test_persistence(tmp_path):
    db_path = tmp_path / "persist.db"

    # Open, write, close (implicitly by letting obj die or just opening new one)
    cache1 = ContentCache(db_path=db_path)
    cache1.set_note(Path("f1"), 1, "h1", "{}")

    # Reopen
    cache2 = ContentCache(db_path=db_path)
    assert cache2.get_hash(Path("f1"), 1) == "h1"
