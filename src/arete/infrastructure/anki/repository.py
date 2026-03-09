"""Anki Repository.

Provides direct access to the Anki Collection (SQLite) using the `anki` python library.
From `anki` library calls.
"""

from __future__ import annotations

import os
import pickle
import sqlite3
from pathlib import Path
from typing import cast

from anki.collection import Collection
from anki.models import NotetypeDict

# These may not exist in mocked test environments
try:
    from anki.decks import DeckId
    from anki.notes import NoteId
except ImportError:
    DeckId = int  # type: ignore
    NoteId = int  # type: ignore

from arete.domain.models import AnkiNote


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
                # Fallback to first non-global profile?
                # For now let's error if ambiguous
                raise ValueError("Could not determine last loaded profile.")

            self.profile_name = last_profile
            return self.base_path / last_profile / "collection.anki2"

        finally:
            conn.close()

    def __enter__(self) -> AnkiRepository:
        """Enter context manager and open the Anki collection."""
        path = self._resolve_collection_path()

        # Save CWD because Anki changes it (legacy behavior)
        self._saved_cwd = os.getcwd()

        try:
            self.col = Collection(str(path))
        except Exception as e:
            # Restore CWD if init fails
            os.chdir(self._saved_cwd)
            raise OSError(f"Could not open Anki collection at {path}: {e}") from e

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager and close the Anki collection."""
        if self.col:
            self.col.close()
            self.col = None

        # Restore CWD
        if hasattr(self, "_saved_cwd"):
            os.chdir(self._saved_cwd)

    def find_notes(self, query: str) -> list[int]:
        """Return list of note IDs matching the query."""
        if not self.col:
            raise RuntimeError("Collection not open")
        return list(self.col.find_notes(query))

    def get_model(self, model_name: str) -> NotetypeDict | None:
        if not self.col:
            raise RuntimeError("Collection not open")
        return self.col.models.by_name(model_name)

    def add_note(self, note_data: AnkiNote) -> int:
        """Add a new note to the collection.

        Returns the new Note ID (nid).
        """
        if not self.col:
            raise RuntimeError("Collection not open")

        model = self.get_model(note_data.model)
        if not model:
            raise ValueError(f"Model '{note_data.model}' not found in Anki.")

        # Ensure we are using this model
        self.col.models.set_current(model)

        note = self.col.new_note(model)

        # Determine deck
        did = self.col.decks.id(note_data.deck)
        if did is None:
            raise RuntimeError(f"Could not resolve deck: {note_data.deck}")
        notetype = note.note_type()
        if notetype is not None:
            notetype["did"] = did

        # Fill fields
        model_field_names = [f["name"] for f in model["flds"]]

        for f_name in model_field_names:
            # Find matching field in note_data (case-insensitive or direct match?)
            # apy logic: note_data.fields dictionary keys.
            # We matched by name.
            if f_name in note_data.fields:
                val = note_data.fields[f_name]
                # Value is already HTML from parser
                note[f_name] = val

        # Add tags
        for tag in note_data.tags:
            note.add_tag(tag)

        # Check for dupes
        if note.duplicate_or_empty():
            print(
                f"Warning: Duplicate note detected for {note_data.fields.get('Front', 'unknown')}"
            )
            # Depending on policy, we might still add it or skip.
            # Anki generally prevents adding dupes via GUI, but API might raise error.
            # collection.add_note returns # changes
            pass

        if did is None:
            raise RuntimeError(f"Could not resolve deck: {note_data.deck}")
        self.col.add_note(note, did)
        return note.id

    def update_note(self, nid: int, note_data: AnkiNote) -> bool:
        """Update an existing note."""
        if not self.col:
            raise RuntimeError("Collection not open")

        try:
            note = self.col.get_note(cast(NoteId, nid))  # type: ignore
        except Exception:
            return False

        # Verify model matches?
        # If model changed, we might need complex change_notetype logic.
        # For v1.3 we assume model matches or we fail safe.
        notetype = note.note_type()
        if notetype is None:
            return False
        current_model = notetype["name"]
        if current_model != note_data.model:
            # We can print a warning but might fail updating fields if schema differs
            print(f"Warning: Model mismatch. Existing: {current_model}, New: {note_data.model}")

        # Update Deck?
        # apy: checks if deck changed, moves cards.
        current_did = note.cards()[0].did
        target_did = self.col.decks.id(note_data.deck)
        if target_did is None:
            raise RuntimeError(f"Could not resolve deck: {note_data.deck}")
        if current_did != target_did:
            cids = [c.id for c in note.cards()]
            self.col.set_deck(cids, int(target_did))

        # Update Fields
        model_field_names = [f["name"] for f in notetype["flds"]]
        changed = False

        for f_name in model_field_names:
            if f_name in note_data.fields:
                new_html = note_data.fields[f_name]
                if note[f_name] != new_html:
                    note[f_name] = new_html
                    changed = True

        # Update Tags
        # Sync tags: remove old, add new? Or merge?
        # Arete typically treats Obsidian as source of truth, so we overwrite tags.
        old_tags = set(note.tags)
        new_tags = set(note_data.tags)

        if old_tags != new_tags:
            note.tags = list(new_tags)
            changed = True

        if changed:
            self.col.update_note(note)
            return True

        return True  # success, just no changes needed
