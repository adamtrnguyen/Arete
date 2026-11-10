"""Anki Repository.

Provides direct access to the Anki Collection (SQLite) using the `anki` python library.
From `anki` library calls.
"""

from __future__ import annotations

import logging
import os
import pickle
import sqlite3
from pathlib import Path

from anki.collection import Collection

logger = logging.getLogger(__name__)


class AnkiRepository:
    """Direct interface to Anki's database."""

    def __init__(self, base_path: Path | None = None, profile_name: str | None = None):
        """Initialize AnkiRepository."""
        if not base_path:
            raise ValueError("Anki base path must be provided.")

        self.base_path = base_path
        self.profile_name = profile_name
        self.col: Collection | None = None
        self._collection_path: Path | None = None
        self._saved_cwd: str | None = None

    def _resolve_collection_path(self) -> Path:
        """Determine the path to collection.anki2 by checking prefs21.db for the active profile."""
        if self._collection_path:
            return self._collection_path

        prefs_db = self.base_path / "prefs21.db"
        if not prefs_db.exists():
            raise FileNotFoundError(f"Anki prefs not found at {prefs_db}")

        # If profile explicitly set, use it
        if self.profile_name:
            return self.base_path / self.profile_name / "collection.anki2"

        # Otherwise read last loaded profile
        conn = sqlite3.connect(prefs_db)
        try:
            res = conn.execute("select cast(data as blob) from profiles where name = '_global'")
            data = res.fetchone()
            if not data:
                raise ValueError("Could not read global profile data from prefs21.db")

            meta = pickle.loads(data[0])
            last_profile = meta.get("last_loaded_profile_name")

            if not last_profile:
                # No profile is guessed: an ambiguous collection is an error.
                raise ValueError("Could not determine last loaded profile.")

            self.profile_name = last_profile
            return self.base_path / last_profile / "collection.anki2"

        finally:
            conn.close()

    def __enter__(self) -> AnkiRepository:
        """Enter context manager and open the Anki collection."""
        path = self._resolve_collection_path()

        # Save CWD: opening a Collection changes it.
        self._saved_cwd = os.getcwd()

        try:
            self.col = Collection(str(path))
        except Exception as e:
            # Restore CWD if init fails
            os.chdir(self._saved_cwd)
            hint = ""
            if "already open" in str(e):
                # Anki holds the collection, so it cannot be opened here: with backend
                # `direct`, or with `auto` when the add-on did not answer in time.
                hint = (
                    " Anki is running and holds the collection. Close Anki, or reach it"
                    " through the arete AnkiConnect add-on (backend auto or ankiconnect,"
                    " with the add-on enabled)."
                )
            raise OSError(f"Could not open Anki collection at {path}: {e}.{hint}") from e

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager and close the Anki collection."""
        if self.col:
            self.col.close()
            self.col = None

        # Restore CWD
        if self._saved_cwd is not None:
            os.chdir(self._saved_cwd)
            self._saved_cwd = None

    def find_notes(self, query: str) -> list[int]:
        """Return list of note IDs matching the query."""
        if not self.col:
            raise RuntimeError("Collection not open")
        return list(self.col.find_notes(query))
