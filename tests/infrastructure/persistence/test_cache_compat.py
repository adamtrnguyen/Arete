"""A cache file written by an earlier Arete opens and keeps working without manual steps.

`fixtures/cache_6653b9f.db` was written by Arete at commit 6653b9f: four syncs of
tests/mock_vault through the direct backend. The schema version is unchanged, so its
rows must survive the open and be read back as they were written.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from arete.application.sync.vault_service import NOT_ARETE_META
from arete.infrastructure.persistence.cache import SCHEMA_VERSION, ContentCache

FIXTURE = Path(__file__).parent / "fixtures" / "cache_6653b9f.db"


@pytest.fixture
def old_db(tmp_path: Path) -> Path:
    db = tmp_path / "cache.db"
    shutil.copy(FIXTURE, db)
    return db


def _rows(db: Path, table: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY path").fetchall()
    finally:
        con.close()


def test_an_old_cache_opens_without_a_rebuild(old_db: Path):
    cards, files = _rows(old_db, "cards"), _rows(old_db, "files")
    assert cards and files

    ContentCache(old_db)

    assert _rows(old_db, "cards") == cards
    assert _rows(old_db, "files") == files
    (version,) = sqlite3.connect(old_db).execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION == 2


def test_old_rows_read_back_and_new_rows_fit_beside_them(old_db: Path):
    path, idx, h, note_json = _rows(old_db, "cards")[0]
    fpath, _fh, mtime, size, meta_json = _rows(old_db, "files")[0]
    cache = ContentCache(old_db)

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


def test_set_file_metas_round_trips_every_column(tmp_path: Path):
    cache = ContentCache(tmp_path / "c.db")
    p = Path("/v/a.md")
    cache.set_file_metas([(p, "hash1", {"arete": True, "cards": [1]}, 100.25, 7)])
    assert _rows(tmp_path / "c.db", "files") == [
        (str(p), "hash1", 100.25, 7, '{"arete": true, "cards": [1]}')
    ]
    assert cache.get_file_meta_by_stat(p, 100.25, 7) == {"arete": True, "cards": [1]}
    cache.set_file_metas([])
