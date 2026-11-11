import json
import logging
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from arete.domain.interfaces import ContentCache as ContentCachePort

# Bump when the table layout changes; the cache is rebuilt from the vault on mismatch.
SCHEMA_VERSION = 2


def _key(md_path: Path) -> str:
    """A note's row key: its path as given, with `/` separators on every OS."""
    return Path(md_path).as_posix()


def cache_path(configured: Path | None = None) -> Path:
    """The cache database a run uses: the configured path, else ~/.config/arete/cache.db."""
    return configured if configured is not None else Path.home() / ".config/arete/cache.db"


class ContentCache(ContentCachePort):
    def __init__(self, db_path: Path | None = None):
        """Open the cache database at `db_path`, or at the default `cache_path()`."""
        if db_path is None:
            db_path = cache_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self._lock = threading.Lock()
        self.logger = logging.getLogger(__name__)
        self.logger.debug(f"[cache] Using database: {self.db_path}")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        """Create the schema.

        The cache is regenerable, so a schema change never migrates: on a `user_version`
        mismatch the tables are dropped and rebuilt.
        """
        with self._lock:
            (version,) = self._conn.execute("PRAGMA user_version").fetchone()
            if version != SCHEMA_VERSION:
                if version:
                    self.logger.info(
                        f"[cache] schema v{version} -> v{SCHEMA_VERSION}: rebuilding {self.db_path}"
                    )
                self._conn.execute("DROP TABLE IF EXISTS cards")
                self._conn.execute("DROP TABLE IF EXISTS files")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS cards (
                    path TEXT,
                    idx INTEGER,
                    hash TEXT,
                    note_json TEXT,
                    PRIMARY KEY (path, idx)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    hash TEXT,
                    mtime REAL,
                    size INTEGER,
                    meta_json TEXT
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS i_cards_path ON cards (path)")
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    def get_hash(self, md_path: Path, card_index: int) -> str | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT hash FROM cards WHERE path = ? AND idx = ?", (_key(md_path), card_index)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_note(self, md_path: Path, card_index: int) -> tuple[str, str | None] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT hash, note_json FROM cards WHERE path = ? AND idx = ?",
                (_key(md_path), card_index),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    def set_notes(self, rows: Iterable[tuple[Path, int, str, str]]) -> None:
        """Write many (path, index, hash, note_json) rows in one transaction.

        All rows are written or, if any write fails, none: a failure can only leave
        cards unrecorded, so the next sync checks them again.
        """
        params = [(_key(p), idx, h, nj) for p, idx, h, nj in rows]
        if not params:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO cards (path, idx, hash, note_json) VALUES (?, ?, ?, ?)",
                params,
            )

    def get_file_meta_by_stat(
        self, md_path: Path, mtime: float, size: int
    ) -> dict[str, Any] | None:
        with self._lock:
            # We use an epsilon for mtime because file system vs python float can vary slightly.
            # 1ms tolerance should be plenty.
            cur = self._conn.execute(
                """
                SELECT meta_json FROM files
                WHERE path = ?
                  AND size = ?
                  AND ABS(mtime - ?) < 0.001
                """,
                (_key(md_path), size, mtime),
            )
            row = cur.fetchone()
            if row:
                try:
                    res = json.loads(row[0])
                    self.logger.debug(f"[cache] stat-meta hit for {md_path.name}")
                    return res
                except Exception as e:
                    self.logger.warning(f"[cache] corrupt stat-meta row for {md_path.name}: {e}")
            return None

    def set_file_meta(
        self,
        md_path: Path,
        current_hash: str,
        meta: dict[str, Any],
        mtime: float = 0,
        size: int = 0,
    ):
        json_str = json.dumps(meta)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO files (path, hash, mtime, size, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (_key(md_path), current_hash, mtime, size, json_str),
            )
            self._conn.commit()

    def set_file_metas(self, rows: Iterable[tuple[Path, str, dict[str, Any], float, int]]) -> None:
        """Write many (path, hash, meta, mtime, size) rows in one transaction, all or none."""
        params = [(_key(p), h, mtime, size, json.dumps(meta)) for p, h, meta, mtime, size in rows]
        if not params:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO files (path, hash, mtime, size, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                params,
            )

    def clear(self):
        with self._lock:
            self._conn.execute("DELETE FROM cards")
            self._conn.execute("DELETE FROM files")
            self._conn.commit()

    def close(self) -> None:
        """Close the database. Windows cannot delete or replace a file held open."""
        with self._lock:
            self._conn.close()
