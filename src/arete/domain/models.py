from dataclasses import dataclass
from pathlib import Path


@dataclass
class AnkiDeck:
    """Represents an Anki Deck."""

    name: str
    # Future extensibility: dynamic options, confusion flags, etc.

    @property
    def parents(self) -> list[str]:
        """Return all parent deck names in the '::' hierarchy."""
        parts = self.name.split("::")
        parents_list = []
        for i in range(1, len(parts)):
            parents_list.append("::".join(parts[:i]))
        return parents_list


@dataclass
class AnkiNote:
    """Represents a fully parsed Anki note ready to be sent to the backend.

    Attributes:
        model: The Anki model name (e.g., 'Basic', 'Cloze').
        deck: The destination deck name (supporting nested '::' syntax).
        fields: Mapping of Anki field names to HTML content (pre-rendered by Parser).
        tags: List of tags to associate with the note.
        start_line: First line of the card in the source Markdown.
        end_line: Last line of the card in the source Markdown.
        source_file: Path to the original Markdown file.
        source_index: 1-based index of this card within the source file.
        nid: Existing Note ID from Obsidian frontmatter (if any).
        cid: Existing Card ID from Obsidian frontmatter (if any).
        content_hash: MD5 hash of the fields/tags for cache-aware syncing.
        arete_id: The card's `id:` in the vault; how write-back finds the card again.

    """

    model: str
    deck: str
    fields: dict[str, str]
    tags: list[str]
    start_line: int
    end_line: int

    source_file: Path
    source_index: int

    nid: str | None = None
    cid: str | None = None

    content_hash: str | None = None
    arete_id: str | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict

        d = asdict(self)
        d["source_file"] = str(self.source_file)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AnkiNote":
        d["source_file"] = Path(d["source_file"])
        return cls(**d)


@dataclass
class WorkItem:
    """Carries a note through the pipeline with metadata."""

    note: AnkiNote
    source_file: Path
    source_index: int  # card index in file (1-based)


@dataclass
class UpdateItem:
    """Result of an Anki backend operation. Returned by AnkiBridge implementation.

    Attributes:
        source_file: Identifies which file this result belongs to.
        source_index: Identifies which card in the file this result belongs to.
        new_nid: The Note ID assigned by Anki (string for consistency).
        new_cid: The Card ID assigned by Anki (string for consistency).
        ok: Whether the operation succeeded.
        error: Descriptive error message if ok is False.
        note: The original AnkiNote object (used for cache persistence).

    """

    source_file: Path
    source_index: int
    new_nid: str | None
    new_cid: str | None
    ok: bool
    error: str | None = None
    note: AnkiNote | None = None


class OutcomeUnknown(Exception):
    """A write that is not safe to send twice lost its reply: it may or may not have landed."""


# ---------------------------------------------------------------------------
# What a note store holds and is asked to write (`interfaces.NoteStoreSession`).
# Scheduling (due, interval, ease, queue, suspension, flags, review history) is absent on
# purpose: a sync can neither read nor write it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeldCard:
    """One card of a note as Anki holds it.

    `home_deck` is the deck the card belongs to: the deck it returns to while it sits in a
    filtered deck. None means the store cannot prove it (a failed read, or a deck reported
    under a stand-in name), which counts as different from any deck.
    `in_filtered` is True while the card sits in a filtered deck, None when unknown.
    """

    cid: int
    ord: int
    home_deck: str | None
    in_filtered: bool | None = None


@dataclass(frozen=True)
class HeldNote:
    """A note as Anki holds it.

    `fields` is in the note type's field order. `cards` is in `ord` order; a note read
    only to find it (`find_by_arete_ids`, `scan_model`) may carry no cards.
    """

    nid: int
    model: str
    fields: dict[str, str]
    tags: tuple[str, ...]
    cards: tuple[HeldCard, ...] = ()


@dataclass(frozen=True)
class NoteTypeInfo:
    """A note type: its exact name, its fields in order, and how many cards a note gets.

    A cloze type makes one card per cloze number; any other type one card per template.
    """

    name: str
    fields: tuple[str, ...]
    is_cloze: bool
    n_templates: int


@dataclass(frozen=True)
class NewNote:
    """A note to create. `fields` holds only fields of the type, under the type's names."""

    model: str
    deck: str
    fields: dict[str, str]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class NoteChange:
    """Everything one note needs to equal the vault, applied in field order.

    `retype` is the target type and, per field of it, the index of the note's current
    field it takes (-1 for none): the shape of Anki's change-notetype request.
    `home_deck` is the cards to file under the deck, and the deck. A card in a filtered
    deck stays there: only the deck it returns to changes.
    """

    nid: int
    retype: tuple[str, tuple[int, ...]] | None = None
    fields: dict[str, str] | None = None
    add_tags: tuple[str, ...] = ()
    remove_tags: tuple[str, ...] = ()
    home_deck: tuple[tuple[int, ...], str] | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.retype or self.fields or self.add_tags or self.remove_tags or self.home_deck
        )
