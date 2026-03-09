from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import AnkiCardStats, AnkiDeck, UpdateItem, WorkItem


@runtime_checkable
class ContentCache(Protocol):
    """Protocol for the file-content cache."""

    def get_file_meta_by_stat(
        self, md_path: Path, mtime: float, size: int
    ) -> dict[str, Any] | None: ...

    def set_file_meta(
        self,
        md_path: Path,
        current_hash: str,
        meta: dict[str, Any],
        mtime: float = 0.0,
        size: int = 0,
    ) -> None: ...

    def set_hash(self, md_path: Path, card_index: int, content_hash: str) -> None: ...

    def get_hash(self, md_path: Path, card_index: int) -> str | None: ...

    def get_note(self, md_path: Path, card_index: int) -> tuple[str, str | None] | None: ...

    def set_note(
        self, md_path: Path, card_index: int, content_hash: str, note_json: str
    ) -> None: ...

    def clear(self) -> None: ...


class AnkiBridge(ABC):
    """Abstract interface for Anki backend operations.

    Implementations (Adapters) are responsible for translating domain-level
    WorkItems into backend-specific commands (e.g., HTTP for AnkiConnect
    or SQLite/CLI for apy).
    """

    @property
    @abstractmethod
    def is_sequential(self) -> bool:
        """Whether this bridge requires sequential access."""
        pass

    @abstractmethod
    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        """Process a batch of notes: add new ones or update existing ones.

        If a note has an existing 'nid', the adapter should attempt to
        update the existing note. If no 'nid' is provided, it should create
         a new one.

        Note: AnkiConnect implementation additionally performs 'Self-Healing'
        by searching for duplicate content if creation fails.
        """
        pass

    @abstractmethod
    async def get_model_names(self) -> list[str]:
        """Return available model types currently installed in Anki."""
        pass

    @abstractmethod
    async def ensure_deck(self, deck: AnkiDeck | str) -> bool:
        """Ensure the named deck exists.

        Implementations should handle nested '::' hierarchies.
        """
        pass

    @abstractmethod
    async def get_deck_names(self) -> list[str]:
        """Return list of all deck names present in Anki."""
        pass

    @abstractmethod
    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        """Return mapping of {obsidian_nid: anki_nid} for all notes in a deck.

        Used primarily by the Pruning stage to identify orphaned cards.
        """
        pass

    @abstractmethod
    async def delete_notes(self, nids: list[int]) -> bool:
        """Permanently delete specified note IDs from Anki."""
        pass

    @abstractmethod
    async def delete_decks(self, names: list[str]) -> bool:
        """Permanently delete specified decks (and their notes) from Anki."""
        pass

    @abstractmethod
    async def get_learning_insights(self, lapse_threshold: int = 3) -> Any:
        """Fetch learning statistics and identify problematic notes.

        Returns a LearningStats-compatible object or data.
        """
        pass

    @abstractmethod
    async def get_card_stats(self, nids: list[int]) -> list[AnkiCardStats]:
        """Fetch detailed statistics for a list of Note IDs.

        Used by the dashboard to show lapses, difficulty, etc.
        """
        pass

    @abstractmethod
    async def gui_browse(self, query: str) -> bool:
        """Open the Anki browser with the specified search query."""
        pass

    @abstractmethod
    async def suspend_cards(self, cids: list[int]) -> bool:
        """Suspend specified card IDs in Anki."""
        pass

    @abstractmethod
    async def unsuspend_cards(self, cids: list[int]) -> bool:
        """Unsuspend specified card IDs in Anki."""
        pass

    @abstractmethod
    async def get_model_styling(self, model_name: str) -> str:
        """Fetch CSS styling for a specific Anki model."""
        pass

    @abstractmethod
    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        """Fetch front/back templates for all cards in an Anki model."""
        pass

    @abstractmethod
    async def create_topo_deck(
        self, deck_name: str, cids: list[int], reschedule: bool = True
    ) -> bool:
        """Create a filtered deck with topological ordering enforced."""
        pass

    @abstractmethod
    async def get_card_ids_for_arete_ids(self, arete_ids: list[str]) -> list[int]:
        """Resolve Arete IDs (e.g. arete_123) to Anki Card IDs (CIDs)."""
        pass

    @abstractmethod
    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Fetch NIDs of due cards, optionally filtered by deck.

        If include_new is True, also includes new (unreviewed) cards.
        """
        pass

    @abstractmethod
    async def find_all_arete_nids(self) -> list[int]:
        """Find all note IDs that have arete tags."""
        pass

    @abstractmethod
    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """Convert Anki Note IDs to Arete IDs."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources (e.g. HTTP clients, DB connections)."""
        pass
