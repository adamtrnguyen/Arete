"""Ports: what the application layer is allowed to ask of the outside world.

The four role ports below are what each caller actually uses, so a use-case that only
reads card statistics never holds a handle that can delete decks
(docs/history/anki-bridge-ports.md). Type a use-case on
its role and the type checker refuses any call outside it. `AnkiBridge` stays as
the composite: it is the type of an adapter and of the shared test fake, nothing else.
"""

from abc import abstractmethod
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import UpdateItem, WorkItem


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

    def set_file_metas(self, rows: list[tuple[Path, str, dict[str, Any], float, int]]) -> None:
        """Write (path, hash, meta, mtime, size) rows in one transaction, all or none."""
        ...

    def get_hash(self, md_path: Path, card_index: int) -> str | None: ...

    def get_note(self, md_path: Path, card_index: int) -> tuple[str, str | None] | None: ...

    def set_note(
        self, md_path: Path, card_index: int, content_hash: str, note_json: str
    ) -> None: ...

    def set_notes(self, rows: list[tuple[Path, int, str, str]]) -> None:
        """Write (path, index, hash, note_json) rows in one transaction, all or none.

        The sync calls it once per batch, only with cards Anki has accepted.
        """
        ...

    def clear(self) -> None: ...


@runtime_checkable
class SyncPort(Protocol):
    """What the sync pipeline needs. Used by application/sync only."""

    @abstractmethod
    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        """Process a batch of notes: add new ones or update existing ones.

        If a note has an existing 'nid', the adapter updates that note. If it has
        none, the adapter reconciles by Arete ID tag, then by content, then creates.

        The adapter owns its own concurrency. A caller may issue overlapping
        sync_notes calls; a backend that cannot take them serializes internally.
        """
        pass

    @abstractmethod
    async def get_deck_names(self) -> list[str]:
        """Return list of all deck names present in Anki."""
        pass

    @abstractmethod
    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        """Every note in the deck, keyed by its note id as a string.

        Prune reads the values to find the notes the vault no longer claims.
        """
        pass

    @abstractmethod
    async def get_note_tags(self, nids: list[int]) -> dict[int, list[str]]:
        """Return {anki_nid: tags} for the given notes. A missing note is left out.

        Used by the prune stage: only a note carrying an Arete id tag may be deleted.
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


@runtime_checkable
class QueuePort(Protocol):
    """What the study-queue builder needs. Used by application/queue only."""

    @abstractmethod
    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Fetch NIDs of due cards, optionally filtered by deck.

        If include_new is True, also includes new (unreviewed) cards.
        """
        pass

    @abstractmethod
    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """Convert Anki Note IDs to Arete IDs."""
        pass

    @abstractmethod
    async def get_card_ids_for_arete_ids(self, arete_ids: list[str]) -> list[int]:
        """Resolve Arete IDs (e.g. arete_123) to Anki Card IDs (CIDs)."""
        pass

    @abstractmethod
    async def create_topo_deck(
        self, deck_name: str, cids: list[int], reschedule: bool = True
    ) -> bool:
        """Create a filtered deck with topological ordering enforced."""
        pass


@runtime_checkable
class CardStatsPort(Protocol):
    """Read-only learning statistics. Used by the learning-insights service.

    Per-card statistics come from the StatsRepository port (domain/stats/ports.py).
    """

    @abstractmethod
    async def get_learning_insights(self, lapse_threshold: int = 3) -> Any:
        """Fetch learning statistics and identify problematic notes.

        Returns a LearningStats-compatible object or data.
        """
        pass


@runtime_checkable
class AnkiAdminPort(Protocol):
    """Direct Anki housekeeping the user asks for by name (suspend, browse, styling)."""

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


class AnkiBridge(SyncPort, QueuePort, CardStatsPort, AnkiAdminPort):
    """Every role at once: the type of an adapter and of the shared test fake.

    Use a role port in a use-case signature. Use this one only where something
    must own the connection itself, which means the composition root.
    """

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources (e.g. HTTP clients, DB connections)."""
        pass
