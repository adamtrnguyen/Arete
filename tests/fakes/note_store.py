"""An in-memory `NoteStorePort`: a collection the sync use-case can be tested against.

It keeps what the port can see (note types, decks, notes, cards and where each card is
filed) and nothing it cannot: no scheduling, no review history. A card in a filtered deck
has a home deck (`odid`) beside the deck it sits in, as in Anki.

It also polices the port's contract, so a use-case bug fails loudly here:
- a primitive called outside a session raises;
- in `read_only` mode any write raises (a dry run must write nothing);
- a type change that would drop cards raises (the use-case must refuse it first);
- a new note with a field its type lacks raises (the use-case sends only the type's).

`fail(op, key)` injects a per-item error: `op` names the primitive, or the step of
`apply_changes` (`retype`, `fields`, `add_tags`, `remove_tags`, `home_deck`), and `key` is
the nid, the `NewNote`, the deck name or the Arete id. `lose(key)` makes a `create_notes`
item land and then report `OutcomeUnknown`, as a lost reply does.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from arete.domain.models import (
    HeldCard,
    HeldNote,
    NewNote,
    NoteChange,
    NoteTypeInfo,
    OutcomeUnknown,
)

WRITES = ("add_field", "create_decks", "create_notes", "apply_changes")

STOCK_TYPES = {
    "Basic": NoteTypeInfo("Basic", ("Front", "Back"), False, 1),
    "Basic (and reversed card)": NoteTypeInfo(
        "Basic (and reversed card)", ("Front", "Back"), False, 2
    ),
    "Basic with Extra": NoteTypeInfo("Basic with Extra", ("Front", "Back", "Back Extra"), False, 1),
    "Cloze": NoteTypeInfo("Cloze", ("Text", "Back Extra"), True, 1),
}


@dataclass
class FakeNote:
    model: str
    fields: dict[str, str]
    tags: list[str]
    cards: list[int] = field(default_factory=list)


@dataclass
class FakeCard:
    nid: int
    ord: int
    did: str  # the deck the card sits in
    odid: str | None = None  # the deck it returns to, while `did` is a filtered deck


class FakeNoteStore:
    def __init__(self, types: dict[str, NoteTypeInfo] | None = None):
        """An empty collection with the stock note types and the Default deck."""
        self.types: dict[str, NoteTypeInfo] = dict(types or STOCK_TYPES)
        self.decks: set[str] = {"Default"}
        self.filtered: set[str] = set()
        self.notes: dict[int, FakeNote] = {}
        self.cards: dict[int, FakeCard] = {}
        self.calls: Counter[str] = Counter()
        self.log: list[tuple[str, Any]] = []
        self.sessions = 0
        self.read_only = False
        self.fail: Callable[[str, Any], Exception | None] = lambda op, key: None
        self.lose: Callable[[NewNote], bool] = lambda note: False
        self._open = 0
        self._next = 1000

    # -- seeding, as a person would in Anki ----------------------------------

    def add(
        self,
        model: str = "Basic",
        deck: str = "Default",
        fields: dict[str, str] | None = None,
        tags: Collection[str] = (),
        n_cards: int | None = None,
    ) -> int:
        info = self.types[model]
        self._next += 1
        nid = self._next
        values = {name: (fields or {}).get(name, "") for name in info.fields}
        note = FakeNote(model, values, list(tags))
        self.notes[nid] = note
        self.decks.add(deck)
        count = n_cards if n_cards is not None else self._cards_for(info, values)
        for ord_ in range(count):
            cid = nid * 10 + ord_
            note.cards.append(cid)
            self.cards[cid] = FakeCard(nid, ord_, deck)
        return nid

    def queue(self, nids: Collection[int], deck: str = "Arete::Queue") -> None:
        """Pull the notes' cards into a filtered deck, keeping their home decks."""
        self.decks.add(deck)
        self.filtered.add(deck)
        for nid in nids:
            for cid in self.notes[nid].cards:
                card = self.cards[cid]
                card.odid, card.did = card.odid or card.did, deck

    def held(self, nid: int) -> HeldNote:
        note = self.notes[nid]
        cards = tuple(
            HeldCard(cid, c.ord, c.odid or c.did, c.odid is not None)
            for cid in note.cards
            if (c := self.cards[cid])
        )
        return HeldNote(nid, note.model, dict(note.fields), tuple(note.tags), cards)

    @staticmethod
    def _cards_for(info: NoteTypeInfo, fields: dict[str, str]) -> int:
        if not info.is_cloze:
            return info.n_templates
        first = fields.get(info.fields[0], "")
        return max(1, len(set(re.findall(r"\{\{c(\d+)::", first))))

    # -- the port ------------------------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncIterator[FakeNoteStore]:
        self.sessions += 1
        self._open += 1
        try:
            yield self
        finally:
            self._open -= 1

    def _enter(self, op: str, arg: Any = None) -> None:
        if not self._open:
            raise RuntimeError(f"{op} called outside a session")
        if self.read_only and op in WRITES:
            raise AssertionError(f"{op} in a read-only run")
        self.calls[op] += 1
        self.log.append((op, arg))

    def _failure(self, op: str, key: Any) -> Exception | None:
        return self.fail(op, key)

    async def note_types(
        self, names: Collection[str]
    ) -> dict[str, NoteTypeInfo | None | Exception]:
        self._enter("note_types", sorted(names))
        out: dict[str, NoteTypeInfo | None | Exception] = {}
        for name in names:
            out[name] = self._failure("note_types", name) or self.types.get(name)
        return out

    async def add_field(self, model: str, field_name: str) -> None:
        self._enter("add_field", (model, field_name))
        if error := self._failure("add_field", model):
            raise error
        info = self.types[model]
        if field_name in info.fields:
            return
        self.types[model] = NoteTypeInfo(
            info.name, (*info.fields, field_name), info.is_cloze, info.n_templates
        )
        for note in self.notes.values():
            if note.model == model:
                note.fields[field_name] = ""

    async def deck_names(self) -> set[str]:
        self._enter("deck_names")
        return set(self.decks)

    async def create_decks(self, names: Collection[str]) -> dict[str, Exception | None]:
        self._enter("create_decks", sorted(names))
        out: dict[str, Exception | None] = {}
        for name in names:
            error = self._failure("create_decks", name)
            if error is None:
                self.decks.add(name)
            out[name] = error
        return out

    async def read_notes(self, nids: Sequence[int]) -> dict[int, HeldNote | None | Exception]:
        self._enter("read_notes", list(nids))
        return {
            nid: self._failure("read_notes", nid) or (self.held(nid) if nid in self.notes else None)
            for nid in nids
        }

    async def find_by_arete_ids(
        self, arete_ids: Sequence[str]
    ) -> dict[str, list[HeldNote] | Exception]:
        self._enter("find_by_arete_ids", list(arete_ids))
        out: dict[str, list[HeldNote] | Exception] = {}
        for arete_id in arete_ids:
            error = self._failure("find_by_arete_ids", arete_id)
            key = arete_id.casefold()
            out[arete_id] = error or [
                self.held(nid)
                for nid in sorted(self.notes)
                if key in {t.casefold() for t in self.notes[nid].tags}
            ]
        return out

    async def scan_model(self, model: str) -> list[HeldNote]:
        self._enter("scan_model", model)
        if error := self._failure("scan_model", model):
            raise error
        return [self.held(nid) for nid in sorted(self.notes) if self.notes[nid].model == model]

    async def create_notes(self, notes: Sequence[NewNote]) -> list[int | Exception]:
        self._enter("create_notes", list(notes))
        out: list[int | Exception] = []
        for new in notes:
            error = self._failure("create_notes", new)
            if error is not None:
                out.append(error)
                continue
            info = self.types.get(new.model)
            if info is None:
                out.append(Exception(f"model was not found: {new.model}"))
                continue
            if new.deck not in self.decks:
                out.append(Exception(f"deck was not found: {new.deck}"))
                continue
            extra = set(new.fields) - set(info.fields)
            if extra:
                raise AssertionError(f"fields the type {new.model} lacks: {sorted(extra)}")
            nid = self.add(new.model, new.deck, new.fields, new.tags)
            out.append(OutcomeUnknown("reply lost") if self.lose(new) else nid)
        return out

    async def apply_changes(self, changes: Sequence[NoteChange]) -> list[Exception | None]:
        self._enter("apply_changes", list(changes))
        return [self._apply(change) for change in changes]

    def _apply(self, change: NoteChange) -> Exception | None:
        note = self.notes.get(change.nid)
        if note is None:
            return Exception(f"note was not found: {change.nid}")
        steps: list[tuple[str, Callable[[], None]]] = []
        if change.retype:
            steps.append(("retype", lambda: self._retype(note, *change.retype)))  # type: ignore[misc]
        if change.fields:
            steps.append(("fields", lambda: self._set_fields(note, change.fields or {})))
        if change.add_tags:
            steps.append(("add_tags", lambda: self._add_tags(note, change.add_tags)))
        if change.remove_tags:
            steps.append(("remove_tags", lambda: self._remove_tags(note, change.remove_tags)))
        if change.home_deck:
            steps.append(("home_deck", lambda: self._file(*change.home_deck)))  # type: ignore[misc]
        for step, run in steps:
            if error := self._failure(step, change.nid):
                return error
            self.log.append((step, change.nid))
            run()
        return None

    def _retype(self, note: FakeNote, model: str, mapping: tuple[int, ...]) -> None:
        target = self.types[model]
        keeps = len(note.cards) if target.is_cloze else target.n_templates
        if len(note.cards) > keeps:
            raise AssertionError(f"retype to {model} would delete cards")
        old = list(note.fields.values())
        note.fields = {
            name: old[i] if i >= 0 else "" for name, i in zip(target.fields, mapping, strict=True)
        }
        note.model = model

    @staticmethod
    def _set_fields(note: FakeNote, fields: dict[str, str]) -> None:
        for name, value in fields.items():
            if name in note.fields:
                note.fields[name] = value

    @staticmethod
    def _add_tags(note: FakeNote, tags: Collection[str]) -> None:
        for tag in tags:
            if tag.casefold() not in {t.casefold() for t in note.tags}:
                note.tags.append(tag)

    @staticmethod
    def _remove_tags(note: FakeNote, tags: Collection[str]) -> None:
        gone = {t.casefold() for t in tags}
        note.tags = [t for t in note.tags if t.casefold() not in gone]

    def _file(self, cids: Collection[int], deck: str) -> None:
        if deck not in self.decks:
            raise AssertionError(f"home deck {deck} does not exist")
        for cid in cids:
            card = self.cards[cid]
            if card.odid is not None:
                card.odid = deck
            else:
                card.did = deck
