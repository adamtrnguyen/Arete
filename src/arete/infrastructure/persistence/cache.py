import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any


class ContentCache:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            # Default to XDG-ish standard: ~/.config/arete/cache.db
            # Fallback to old path if the cachedb already exists there?
            # No, user asked to "make everything inside .config/arete"
            conf_dir = Path.home() / ".config/arete"
            conf_dir.mkdir(parents=True, exist_ok=True)
            db_path = conf_dir / "cache.db"

        self.db_path = db_path
        self._lock = threading.Lock()
        self.logger = logging.getLogger(__name__)
        self.logger.debug(f"[cache] Using database: {self.db_path}")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS cards (
                    path TEXT,
                    idx INTEGER,
                    hash TEXT,
                    note_json TEXT,
                    PRIMARY KEY (path, idx)
                )
            """)
            # Schema migration for existing 'cards' table
            try:
                self._conn.execute("SELECT note_json FROM cards LIMIT 1")
            except sqlite3.OperationalError:
                self.logger.info("Adding 'note_json' column to 'cards' table...")
                self._conn.execute("ALTER TABLE cards ADD COLUMN note_json TEXT")
            # Ensure 'files' table has 'mtime' column (schema migration)
            try:
                self._conn.execute("SELECT mtime FROM files LIMIT 1")
            except sqlite3.OperationalError:
                self.logger.info("Upgrading cache database schema...")
                self._conn.execute("DROP TABLE IF EXISTS files")
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
            self._conn.commit()

    def get_hash(self, md_path: Path, card_index: int) -> str | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT hash FROM cards WHERE path = ? AND idx = ?", (str(md_path), card_index)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_note(self, md_path: Path, card_index: int) -> tuple[str, str | None] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT hash, note_json FROM cards WHERE path = ? AND idx = ?",
                (str(md_path), card_index),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    def set_hash(self, md_path: Path, card_index: int, content_hash: str):
        with self._lock:
            self._conn.execute(
                """INSERT INTO cards (path, idx, hash) VALUES (?, ?, ?)
                   ON CONFLICT(path, idx) DO UPDATE SET hash = excluded.hash""",
                (str(md_path), card_index, content_hash),
            )
            self._conn.commit()

    def set_note(self, md_path: Path, card_index: int, content_hash: str, note_json: str):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cards (path, idx, hash, note_json) VALUES (?, ?, ?, ?)",
                (str(md_path), card_index, content_hash, note_json),
            )
            self._conn.commit()

    def get_file_meta(self, md_path: Path, current_hash: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT meta_json FROM files WHERE path = ? AND hash = ?",
                (str(md_path), current_hash),
            )
            row = cur.fetchone()
            if row:
                try:
                    res = json.loads(row[0])
                    self.logger.debug(f"[cache] meta hit for {md_path.name}")
                    return res
                except Exception:
                    pass
            self.logger.debug(f"[cache] meta miss for {md_path.name}")
            return None

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
                (str(md_path), size, mtime),
            )
            row = cur.fetchone()
            if row:
                try:
                    res = json.loads(row[0])
                    self.logger.debug(f"[cache] stat-meta hit for {md_path.name}")
                    return res
                except Exception:
                    pass
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
                (str(md_path), current_hash, mtime, size, json_str),
            )
            self._conn.commit()

    def clear(self):
        with self._lock:
            self._conn.execute("DELETE FROM cards")
            self._conn.execute("DELETE FROM files")
            self._conn.commit()
