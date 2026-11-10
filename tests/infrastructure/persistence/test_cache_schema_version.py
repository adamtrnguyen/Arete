"""The cache's `SCHEMA_VERSION` check: a file at the version keeps its rows, any other is rebuilt.

`fixtures/cache_6653b9f.db` is a real cache file (four syncs of tests/mock_vault through
the direct backend, written at commit 6653b9f) at the current schema version. A table
change made without bumping `SCHEMA_VERSION` fails here: the fixture's rows no longer
read back.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from arete.application.sync.vault_service import NOT_ARETE_META
from arete.infrastructure.persistence.cache import SCHEMA_VERSION, ContentCache

FIXTURE = Path(__file__).parent / "fixtures" / "cache_6653b9f.db"


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "cache.db"
    shutil.copy(FIXTURE, db)
    return db


def _rows(db: Path, table: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY path").fetchall()
    finally:
        con.close()


def test_a_cache_at_the_schema_version_keeps_its_rows(fixture_db: Path):
    cards, files = _rows(fixture_db, "cards"), _rows(fixture_db, "files")
    assert cards and files

    ContentCache(fixture_db)

    assert _rows(fixture_db, "cards") == cards
    assert _rows(fixture_db, "files") == files
    (version,) = sqlite3.connect(fixture_db).execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION == 2


def test_its_rows_read_back_and_new_rows_fit_beside_them(fixture_db: Path):
    path, idx, h, note_json = _rows(fixture_db, "cards")[0]
    fpath, _fh, mtime, size, meta_json = _rows(fixture_db, "files")[0]
    cache = ContentCache(fixture_db)

    assert cache.get_note(Path(path), idx) == (h, note_json)
    meta = cache.get_file_meta_by_stat(Path(fpath), mtime, size)
    assert meta is not None and meta["arete"] is True

    plain = Path("/v/plain.md")
    cache.set_file_metas([(plain, "", NOT_ARETE_META, 12.5, 40)])
    cache.set_notes([(Path("/v/new.md"), 1, "h", "{}")])
    assert cache.get_file_meta_by_stat(plain, 12.5, 40) == NOT_ARETE_META
    assert cache.get_file_meta_by_stat(plain, 12.5, 41) is None
    assert cache.get_hash(Path("/v/new.md"), 1) == "h"
    assert cache.get_note(Path(path), idx) == (h, note_json)


def test_a_cache_at_another_schema_version_is_dropped_and_rebuilt(fixture_db: Path):
    con = sqlite3.connect(fixture_db)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    con.commit()
    con.close()

    ContentCache(fixture_db)

    assert _rows(fixture_db, "cards") == []
    assert _rows(fixture_db, "files") == []
    (version,) = sqlite3.connect(fixture_db).execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION


def test_set_file_metas_round_trips_every_column(tmp_path: Path):
    cache = ContentCache(tmp_path / "c.db")
    p = Path("/v/a.md")
    cache.set_file_metas([(p, "hash1", {"arete": True, "cards": [1]}, 100.25, 7)])
    assert _rows(tmp_path / "c.db", "files") == [
        (str(p), "hash1", 100.25, 7, '{"arete": true, "cards": [1]}')
    ]
    assert cache.get_file_meta_by_stat(p, 100.25, 7) == {"arete": True, "cards": [1]}
    cache.set_file_metas([])
