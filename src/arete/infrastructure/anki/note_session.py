"""The note-store primitives over an open collection, for the direct backend.

Reads go through SQL, 500 ids per query, so a batch costs a few queries whatever its
size. Writes go through the anki library. A card's home deck is `odid` while it sits in
a filtered deck and `did` otherwise; a new home deck is written to the same column, so
a queued card stays queued and its scheduling is untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Collection as Names
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from arete.domain.interfaces import NoteStoreSession
from arete.domain.models import HeldCard, HeldNote, NewNote, NoteChange, NoteTypeInfo
from arete.infrastructure.anki.search import search_term

if TYPE_CHECKING:
    from anki.collection import Collection

CHUNK = 500
_FIELD_SEP = "\x1f"
_CLOZE = 1  # notetype "type" of a cloze type


def _qs(n: int) -> str:
    return ",".join("?" * n)


class DirectNoteSession(NoteStoreSession):
    """`NoteStoreSession` over a collection the caller holds open."""

    def __init__(self, col: Collection, logger: logging.Logger | None = None):
        """Read and write `col`; the caller opens and closes it."""
        self.col = col
        self.logger = logger or logging.getLogger(__name__)
        self._deck_names: dict[int, str | None] = {}
        self._models: dict[int, Any] = {}

    @property
    def _db(self) -> Any:
        if self.col.db is None:
            raise OSError("Anki collection is closed")
        return self.col.db

    # -- reads ---------------------------------------------------------------

    async def note_types(self, names: Names[str]) -> dict[str, NoteTypeInfo | None | Exception]:
        out: dict[str, NoteTypeInfo | None | Exception] = {}
        for name in names:
            try:
                model = self.col.models.by_name(name)
                out[name] = None if model is None else _type_info(model)
            except Exception as e:
                out[name] = e
        return out

    async def deck_names(self) -> set[str]:
        return {d.name for d in self.col.decks.all_names_and_ids(include_filtered=True)}

    async def read_notes(self, nids: Sequence[int]) -> dict[int, HeldNote | None | Exception]:
        out: dict[int, HeldNote | None | Exception] = {}
        unique = list(dict.fromkeys(int(n) for n in nids))
        for i in range(0, len(unique), CHUNK):
            chunk = unique[i : i + CHUNK]
            try:
                held = self._read_chunk(chunk)
                out.update((nid, held.get(nid)) for nid in chunk)
            except Exception as e:
                out.update((nid, e) for nid in chunk)
        return out

    async def find_by_arete_ids(
        self, arete_ids: Sequence[str]
    ) -> dict[str, list[HeldNote] | Exception]:
        out: dict[str, list[HeldNote] | Exception] = {}
        for arete_id in arete_ids:
            try:
                nids = sorted(self.col.find_notes(search_term("tag", arete_id)))
                key = arete_id.casefold()
                held = self._read_chunk([int(n) for n in nids])
                out[arete_id] = [
                    h for n in nids if (h := held.get(int(n))) and key in _folded(h.tags)
                ]
            except Exception as e:
                out[arete_id] = e
        return out

    async def scan_model(self, model: str) -> list[HeldNote]:
        notetype = self.col.models.by_name(model)
        if notetype is None:
            return []
        nids = self._db.list("select id from notes where mid = ? order by id", notetype["id"])
        found: list[HeldNote] = []
        for i in range(0, len(nids), CHUNK):
            held = self._read_chunk(nids[i : i + CHUNK], with_cards=False)
            found.extend(held[n] for n in nids[i : i + CHUNK] if n in held)
        return found

    def _read_chunk(self, nids: list[int], with_cards: bool = True) -> dict[int, HeldNote]:
        if not nids:
            return {}
        rows = self._db.all(
            f"select id, mid, flds, tags from notes where id in ({_qs(len(nids))})", *nids
        )
        cards: dict[int, list[HeldCard]] = {}
        if with_cards:
            for cid, nid, ord_, did, odid in self._db.all(
                f"select id, nid, ord, did, odid from cards where nid in ({_qs(len(nids))}) "
                "order by nid, ord",
                *nids,
            ):
                home = self._deck_name(odid or did)
                cards.setdefault(nid, []).append(HeldCard(cid, ord_, home, bool(odid)))
        out: dict[int, HeldNote] = {}
        for nid, mid, flds, tags in rows:
            model = self._model(mid)
            names = [f["name"] for f in model["flds"]] if model else []
            values = flds.split(_FIELD_SEP)
            out[nid] = HeldNote(
                nid,
                model["name"] if model else "",
                dict(zip(names, values, strict=False)),
                tuple(t for t in tags.split(" ") if t),  # a tag may hold a no-break space
                tuple(cards.get(nid, [])),
            )
        return out

    def _model(self, mid: int) -> Any:
        if mid not in self._models:
            self._models[mid] = self.col.models.get(cast(Any, mid))
        return self._models[mid]

    def _deck_name(self, did: int) -> str | None:
        """The deck's name, or None for a deck the collection does not hold."""
        if did not in self._deck_names:
            deck = self.col.decks.get(cast(Any, did), default=False)
            self._deck_names[did] = deck["name"] if deck else None
        return self._deck_names[did]

    # -- writes --------------------------------------------------------------

    async def add_field(self, model: str, field: str) -> None:
        notetype = self.col.models.by_name(model)
        if notetype is None:
            raise ValueError(f"model was not found: {model}")
        if field in [f["name"] for f in notetype["flds"]]:
            return
        self.col.models.add_field(notetype, self.col.models.new_field(field))
        self.col.models.update_dict(notetype)
        self._models.pop(notetype["id"], None)

    async def create_decks(self, names: Names[str]) -> dict[str, Exception | None]:
        out: dict[str, Exception | None] = {}
        for name in names:
            try:
                self.col.decks.id(name)
                out[name] = None
            except Exception as e:
                out[name] = e
        self._deck_names.clear()
        return out

    async def create_notes(self, notes: Sequence[NewNote]) -> list[int | Exception]:
        out: list[int | Exception] = []
        for new in notes:
            try:
                out.append(self._create(new))
            except Exception as e:
                out.append(e)
        return out

    def _create(self, new: NewNote) -> int:
        notetype = self.col.models.by_name(new.model)
        if notetype is None:
            raise ValueError(f"model was not found: {new.model}")
        did = self.col.decks.id(new.deck, create=False)
        if did is None:
            raise ValueError(f"deck was not found: {new.deck}")
        note = self.col.new_note(notetype)
        for name, value in new.fields.items():
            note[name] = value
        note.tags = list(new.tags)
        self.col.add_note(note, did)
        return int(note.id)

    async def apply_changes(self, changes: Sequence[NoteChange]) -> list[Exception | None]:
        out: list[Exception | None] = []
        for change in changes:
            try:
                self._apply(change)
                out.append(None)
            except Exception as e:
                self.logger.error(f"[anki] note {change.nid}: {e}")
                out.append(e)
        return out

    def _apply(self, change: NoteChange) -> None:
        """Retype, then fields and tags in one note write, then the home deck."""
        nid = cast(Any, change.nid)
        if change.retype:
            self._retype(change.nid, *change.retype)
        if change.fields or change.add_tags or change.remove_tags:
            note = self.col.get_note(nid)
            for name, value in (change.fields or {}).items():
                if name in note:
                    note[name] = value
            present = _folded(note.tags)
            note.tags = [*note.tags, *(t for t in change.add_tags if t.casefold() not in present)]
            gone = _folded(change.remove_tags)
            note.tags = [t for t in note.tags if t.casefold() not in gone]
            self.col.update_note(note)
        if change.home_deck:
            self._file(*change.home_deck)

    def _retype(self, nid: int, model: str, mapping: tuple[int, ...]) -> None:
        old = self.col.get_note(cast(Any, nid)).note_type()
        new = self.col.models.by_name(model)
        if old is None or new is None:
            raise ValueError(f"model was not found: {model}")
        request = self.col.models.change_notetype_info(
            old_notetype_id=old["id"], new_notetype_id=new["id"]
        ).input
        request.note_ids.append(nid)
        request.new_fields[:] = list(mapping)
        self.col.models.change_notetype_of_notes(request)
        self.logger.info(f"[anki] note {nid}: note type {old['name']} -> {model}")

    def _file(self, cids: tuple[int, ...], deck: str) -> None:
        """File the cards under the deck; a queued card's home deck changes, not its queue."""
        did = self.col.decks.id(deck, create=False)
        if did is None:
            raise ValueError(f"deck was not found: {deck}")
        loose = []
        for cid in cids:
            card = self.col.get_card(cast(Any, cid))
            if card.odid:
                card.odid = did
                self.col.update_card(card)
            elif card.did != did:
                loose.append(card.id)
        if loose:
            self.col.set_deck(loose, int(did))


def _type_info(model: Any) -> NoteTypeInfo:
    return NoteTypeInfo(
        model["name"],
        tuple(f["name"] for f in model["flds"]),
        model.get("type") == _CLOZE,
        len(model["tmpls"]),
    )


def _folded(tags: Sequence[str]) -> set[str]:
    return {t.casefold() for t in tags}
