"""Bring Anki's notes to what the vault says: the one copy of the sync decision logic.

`NoteSync` decides, for each vault card, which Anki note it is and what that note needs,
and leaves the doing to a `NoteStorePort`. Both backends implement the port, so whether
Anki is open changes how a note is reached, never what is decided.

Per batch, inside one store session:

1. **By nid.** The note at the card's `anki.nid` is the card's note when it carries the
   card's Arete id. A note carrying another card's id is that card's: the vault's nid is a
   copy and the card is resolved as if it had none. A note carrying an Arete id no vault
   card declares is refused, since creating beside it would orphan a note that may hold
   reviews. A note with no Arete id is taken only when its first field matches. A failed
   read fails the card; it is never taken for a missing note.
2. **By Arete id**, for a card with no nid or a nid Anki does not hold.
3. **By content**, the first field, for a card still unresolved. A note carrying another
   card's Arete id, or claimed by another card, is never taken.
4. **Create** the rest. A create whose reply was lost is looked up by its Arete id and
   never sent again in the same run.
5. **Compare and write.** Fields, tags, home deck and note type are set to the vault's
   where they differ. Tags compare case-insensitively, and the tags Anki owns
   (`ANKI_OWNED_TAGS`) are never removed. A type change that would drop cards is refused.

One note belongs to at most one card per run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from arete.domain.identity import ANKI_OWNED_TAGS, first_field_key
from arete.domain.interfaces import NoteStorePort, NoteStoreSession
from arete.domain.models import (
    HeldNote,
    NewNote,
    NoteChange,
    NoteTypeInfo,
    OutcomeUnknown,
    UpdateItem,
    WorkItem,
)
from arete.domain.note_types import map_note_type_fields
from arete.domain.ownership import is_arete_id

SOURCE_FIELD = "_obsidian_source"


@dataclass(frozen=True)
class VaultClaims:
    """What the scanned vault declares, for telling a copied id from a foreign one.

    `nid_owners` maps each declared nid to the casefolded Arete ids of the cards
    declaring it (None for a card without one). `arete_ids` holds every declared Arete
    id, casefolded. A run over part of the vault declares less: an id it does not see
    counts as declared by no card, so the card is refused rather than guessed at.
    """

    nid_owners: dict[int, frozenset[str | None]] = field(default_factory=dict)
    arete_ids: frozenset[str] = frozenset()


@dataclass
class _Card:
    """One work item on its way through a batch."""

    work: WorkItem
    own: str | None  # the card's Arete id, casefolded
    nid: int | None = None  # the note it resolved to
    held: HeldNote | None = None  # that note as read, with its cards
    how: str = "update"  # update | adopt | create
    error: str | None = None

    @property
    def label(self) -> str:
        return f"{self.work.source_file.name} #{self.work.source_index}"

    @property
    def lookup_id(self) -> str | None:
        """The card's Arete id when it has the documented form; only that is looked up."""
        arete_id = self.work.note.arete_id
        return arete_id if arete_id and is_arete_id(arete_id) else None

    @property
    def open(self) -> bool:
        return self.error is None and self.nid is None

    def fail(self, error: object) -> None:
        if self.error is None:
            self.error = str(error)


def _key_of(arete_id: str | None) -> str | None:
    return arete_id.strip().casefold() if arete_id and arete_id.strip() else None


def _type_fields(info: NoteTypeInfo, fields: dict[str, str]) -> dict[str, str]:
    """The vault's fields under the type's names, in the type's order; others dropped.

    A vault field matches a type field by exact name, else ignoring case.
    """
    by_fold = {name.casefold(): name for name in info.fields}
    out: dict[str, str] = {}
    for name, value in fields.items():
        if name in info.fields:
            out[name] = value
        elif (target := by_fold.get(name.casefold())) and target not in fields:
            out[target] = value
    return {name: out[name] for name in info.fields if name in out}


def _first_key(info: NoteTypeInfo, fields: dict[str, str]) -> str:
    """The content key of the vault card's first field under the type."""
    mapped = _type_fields(info, fields)
    return first_field_key(mapped.get(info.fields[0], "")) if info.fields else ""


def _held_first_key(held: HeldNote) -> str:
    return first_field_key(next(iter(held.fields.values()), ""))


def _arete_ids_of(held: HeldNote) -> set[str]:
    """The note's tags that are Arete ids, casefolded."""
    return {t.casefold() for t in held.tags if is_arete_id(t)}


class NoteSync:
    """The sync use-case: `sync_notes` has the `SyncPort.sync_notes` contract."""

    def __init__(
        self,
        store: NoteStorePort,
        claims: VaultClaims | None = None,
        logger: logging.Logger | None = None,
        dry_run: bool = False,
    ):
        """One instance per run: its caches and claims hold for the run.

        With `dry_run` it reads and decides exactly as a sync does, writes nothing, and
        puts what a sync would do in `report`, one line per card, deck and field.
        """
        self._store = store
        self._claims = claims or VaultClaims()
        self._log = logger or logging.getLogger(__name__)
        self._dry = dry_run
        self.report: list[str] = []
        self._types: dict[str, NoteTypeInfo | None | Exception] = {}
        self._types_lock = asyncio.Lock()
        self._source_tried: set[str] = set()
        self._decks: set[str] | None = None
        self._decks_lock = asyncio.Lock()
        self._index: dict[str, dict[str, list[tuple[int, frozenset[str]]]]] = {}
        self._index_lock = asyncio.Lock()
        self._claimed: dict[int, str] = {}  # nid -> the card that resolved to it this run

    # -- entry ---------------------------------------------------------------

    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        cards = [_Card(w, _key_of(w.note.arete_id)) for w in work_items]
        try:
            async with self._store.session() as s:
                await self._run(s, cards)
        except Exception as e:  # the session itself failed: no card got a decision
            self._log.error(f"[sync] the Anki collection could not be used: {e}")
            for card in cards:
                card.fail(f"Anki collection unavailable: {e}")
        return [self._result(card) for card in cards]

    async def _run(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        await self._load_types(s, {c.work.note.model for c in cards})
        await self._ensure_source_fields(s, cards)
        await self._resolve_by_nid(s, cards)
        await self._resolve_by_id(s, [c for c in cards if c.open])
        await self._resolve_by_content(s, [c for c in cards if c.open])
        await self._create(s, [c for c in cards if c.open])
        await self._read_adopted(
            s,
            [
                c
                for c in cards
                if c.how == "adopt" and c.error is None and not (c.held and c.held.cards)
            ],
        )
        await self._load_types(s, {c.held.model for c in cards if c.held})
        await self._write(s, cards)

    def _plan(self, line: str) -> None:
        """Record and log what a sync would do (a dry run only)."""
        self.report.append(line)
        self._log.info(f"[dry-run] {line}")

    # -- note types and decks, cached for the run ----------------------------

    async def _load_types(self, s: NoteStoreSession, names: Collection[str]) -> None:
        async with self._types_lock:
            # A type whose read failed is read again by the next batch that needs it.
            todo = sorted(
                n for n in names if n not in self._types or isinstance(self._types[n], Exception)
            )
            if not todo:
                return
            try:
                self._types.update(await s.note_types(todo))
            except Exception as e:
                self._log.error(f"[sync] reading note types {todo} failed: {e}")
                return  # not cached: the cards fail now, a later batch reads again

    def _type(self, name: str) -> NoteTypeInfo | str:
        """The type, or why the card cannot be synced."""
        info = self._types.get(name)
        if isinstance(info, NoteTypeInfo):
            return info
        if info is None and name in self._types:
            return f"note type '{name}' not found in Anki"
        return f"note type '{name}' could not be read: {info}"

    async def _ensure_source_fields(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        """Give each type a card sends `_obsidian_source` to that field, once per run."""
        needed = {c.work.note.model for c in cards if SOURCE_FIELD in c.work.note.fields}
        async with self._types_lock:
            for name in sorted(needed - self._source_tried):
                info = self._types.get(name)
                if not isinstance(info, NoteTypeInfo) or SOURCE_FIELD in info.fields:
                    continue
                self._source_tried.add(name)
                if self._dry:
                    self._plan(f"would add field {SOURCE_FIELD} to note type '{info.name}'")
                else:
                    try:
                        await s.add_field(info.name, SOURCE_FIELD)
                    except Exception as e:
                        self._log.warning(f"Could not add {SOURCE_FIELD} to '{info.name}': {e}")
                        continue
                    self._log.info(f"Added '{SOURCE_FIELD}' field to model '{info.name}'")
                self._types[name] = NoteTypeInfo(
                    info.name, (*info.fields, SOURCE_FIELD), info.is_cloze, info.n_templates
                )

    async def _ensure_decks(self, s: NoteStoreSession, names: Collection[str]) -> dict[str, str]:
        """Create the decks Anki lacks; per deck that could not be created, why."""
        async with self._decks_lock:
            try:
                if self._decks is None:
                    self._decks = {d.casefold() for d in await s.deck_names()}
                known = self._decks
                todo = sorted({n for n in names if n.casefold() not in known})
                if self._dry:
                    for name in todo:
                        self._plan(f"would create deck '{name}'")
                    created: dict[str, Exception | None] = dict.fromkeys(todo)
                else:
                    created = await s.create_decks(todo) if todo else {}
            except Exception as e:
                return {n: f"deck '{n}' could not be checked or created: {e}" for n in names}
            failed: dict[str, str] = {}
            for name, error in created.items():
                if error is None:
                    known.add(name.casefold())
                else:
                    failed[name] = f"deck '{name}' could not be created: {error}"
            return failed

    # -- identity ------------------------------------------------------------

    def _claim(self, card: _Card, nid: int, how: str) -> bool:
        """Resolve the card to the note, unless another card resolved to it this run."""
        holder = self._claimed.get(nid)
        if holder is not None and holder != card.label:
            card.fail(f"note {nid} is already synced by {holder} this run")
            return False
        self._claimed[nid] = card.label
        card.nid, card.how = nid, how
        return True

    def _declared_by_another(self, card: _Card, nid: int) -> bool:
        """True when a vault card other than this one declares the nid."""
        owners = self._claims.nid_owners.get(nid)
        if not owners:
            return False
        if card.own is not None:
            return bool(owners - {card.own})
        return (card.work.note.nid or "").strip() != str(nid)

    def _free(self, card: _Card, nid: int) -> bool:
        holder = self._claimed.get(nid)
        taken = holder is not None and holder != card.label
        return not taken and not self._declared_by_another(card, nid)

    async def _resolve_by_nid(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        wanted: dict[int, _Card] = {}
        ordered: list[tuple[_Card, int]] = []
        for card in cards:
            raw = (card.work.note.nid or "").strip()
            if not raw:
                continue
            try:
                nid = int(raw)
            except ValueError:
                card.fail(f"anki.nid {raw!r} is not a note id")
                continue
            ordered.append((card, nid))
            wanted.setdefault(nid, card)
        if not ordered:
            return
        try:
            held = await s.read_notes(list(wanted))
        except Exception as e:
            held = dict.fromkeys(wanted, e)
        for card, nid in ordered:
            self._check_identity(card, nid, held.get(nid))

    def _check_identity(self, card: _Card, nid: int, held: HeldNote | None | Exception) -> None:
        """Decide whether the note at the card's nid is the card's note (module docstring)."""
        if isinstance(held, Exception):
            card.fail(f"reading note {nid} failed: {held}")
            return
        if held is None:
            self._log.warning(
                f"[dangling-nid] {card.label}: nid={nid} not in Anki; reconciling by ID tag "
                "/ content"
            )
            return
        tags = {t.casefold() for t in held.tags}
        if card.own and card.own in tags:
            if self._claim(card, nid, "update"):
                card.held = held
            return
        foreign = sorted(t for t in held.tags if is_arete_id(t) and t.casefold() != card.own)
        if {t.casefold() for t in foreign} & self._claims.arete_ids:
            self._log.warning(
                f"[copied-nid] {card.label}: nid={nid} is the note of another vault card "
                f"({', '.join(foreign)}); reconciling by ID tag / content"
            )
            return
        if foreign:
            card.fail(
                f"anki.nid {nid} points at a note carrying Arete id {foreign[0]}, "
                "which no card in the vault declares. Not syncing: if this card's id was "
                "edited, restore it"
            )
            return
        self._adopt_legacy(card, nid, held)

    def _adopt_legacy(self, card: _Card, nid: int, held: HeldNote) -> None:
        """A note with no Arete id is the card's only if its first field matches."""
        info = self._type(card.work.note.model)
        key = _first_key(info, card.work.note.fields) if isinstance(info, NoteTypeInfo) else ""
        if key and key == _held_first_key(held):
            if self._claim(card, nid, "update"):
                card.held = held
            return
        card.fail(
            f"anki.nid {nid} points at a note Arete does not own: it carries no Arete id "
            "and its first field differs. Not syncing"
        )

    async def _resolve_by_id(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        by_id: dict[str, list[_Card]] = {}
        for card in cards:
            if card.lookup_id:
                by_id.setdefault(card.lookup_id, []).append(card)
        if not by_id:
            return
        try:
            found = await s.find_by_arete_ids(list(by_id))
        except Exception as e:
            found = dict.fromkeys(by_id, e)
        for arete_id, group in by_id.items():
            result = found.get(arete_id, [])
            for card in group:
                if isinstance(result, Exception):
                    card.fail(f"looking up Arete id {arete_id} failed: {result}")
                else:
                    self._adopt_by_id(card, result)

    def _adopt_by_id(self, card: _Card, candidates: list[HeldNote]) -> None:
        free = [h for h in candidates if self._free(card, h.nid)]
        if not free:
            return
        info = self._type(card.work.note.model)
        key = _first_key(info, card.work.note.fields) if isinstance(info, NoteTypeInfo) else ""
        matching = [h for h in free if key and _held_first_key(h) == key]
        chosen = min(matching or free, key=lambda h: h.nid)
        if len(free) > 1:
            self._log.warning(
                f"[heal] tag {card.lookup_id} is on {len(free)} notes; using nid={chosen.nid} "
                "(the rest are duplicates -- prune them)"
            )
        if self._claim(card, chosen.nid, "adopt"):
            card.held = chosen
            self._log.info(f" -> Healed by arete ID {card.lookup_id}: nid={chosen.nid}")

    async def _resolve_by_content(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        for card in cards:
            info = self._type(card.work.note.model)
            if not isinstance(info, NoteTypeInfo):
                continue  # fails at create, with the reason
            key = _first_key(info, card.work.note.fields)
            if not key:
                continue
            index = await self._content_index(s, info.name)
            if isinstance(index, Exception):
                card.fail(f"matching by content failed, scanning '{info.name}': {index}")
                continue
            for nid, ids in index.get(key, []):
                if ids <= {card.own} and self._free(card, nid):
                    if self._claim(card, nid, "adopt"):
                        self._log.info(f" -> Healed! matched existing note: {nid}")
                    break

    async def _content_index(
        self, s: NoteStoreSession, model: str
    ) -> dict[str, list[tuple[int, frozenset[str]]]] | Exception:
        """First-field key -> (nid, Arete ids), oldest first; read once per type per run.

        A failed scan is not kept, so a later batch scans again.
        """
        async with self._index_lock:
            if model not in self._index:
                try:
                    scanned = await s.scan_model(model)
                except Exception as e:
                    return e
                index: dict[str, list[tuple[int, frozenset[str]]]] = {}
                for held in sorted(scanned, key=lambda h: h.nid):
                    if key := _held_first_key(held):
                        index.setdefault(key, []).append((held.nid, frozenset(_arete_ids_of(held))))
                self._index[model] = index
            return self._index[model]

    # -- create --------------------------------------------------------------

    async def _create(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        todo = await self._creatable(s, cards)
        if not todo:
            return
        if self._dry:
            for card, new in todo:
                card.how = "create"
                self._plan(f"would create {card.label} in deck '{new.deck}' ({new.model})")
            return
        try:
            results: list[int | Exception] = await s.create_notes([n for _, n in todo])
        except Exception as e:
            results = [e] * len(todo)
        lost = []
        for (card, _), result in zip(todo, results, strict=True):
            if isinstance(result, OutcomeUnknown):
                lost.append((card, result))
            elif isinstance(result, Exception):
                card.fail(f"Add failed: {result}")
            elif self._claim(card, int(result), "create"):
                self._log.debug(f"[create] {card.label} -> nid={result}")
        await self._adopt_lost(s, lost)

    async def _creatable(
        self, s: NoteStoreSession, cards: list[_Card]
    ) -> list[tuple[_Card, NewNote]]:
        """The notes to create, their decks made first; a card that cannot be is failed."""
        todo: list[tuple[_Card, NewNote]] = []
        for card in cards:
            new = self._new_note(card)
            if new is not None:
                todo.append((card, new))
        failed = await self._ensure_decks(s, {new.deck for _, new in todo}) if todo else {}
        for card, new in todo:
            if new.deck in failed:
                card.fail(failed[new.deck])
        return [(c, n) for c, n in todo if c.error is None]

    def _new_note(self, card: _Card) -> NewNote | None:
        note = card.work.note
        info = self._type(note.model)
        if not isinstance(info, NoteTypeInfo):
            card.fail(info)
            return None
        return NewNote(
            info.name, note.deck, _type_fields(info, note.fields), tuple(dict.fromkeys(note.tags))
        )

    async def _adopt_lost(self, s: NoteStoreSession, lost: list[tuple[_Card, Exception]]) -> None:
        """A create whose reply was lost: the note found by the card's Arete id, if any."""
        if not lost:
            return
        ids = [c.lookup_id for c, _ in lost if c.lookup_id]
        try:
            found = await s.find_by_arete_ids(ids) if ids else {}
        except Exception as e:
            found = dict.fromkeys(ids, e)
        for card, error in lost:
            result = found.get(card.lookup_id or "", [])
            free = (
                []
                if isinstance(result, Exception)
                else [h for h in result if self._free(card, h.nid)]
            )
            landed = min(free, key=lambda h: h.nid) if free else None
            if landed is not None and self._claim(card, landed.nid, "adopt"):
                card.held = landed
                self._log.warning(f"[adopt] {error}; Anki holds {card.lookup_id} as nid={card.nid}")
                continue
            card.fail(
                f"{error}. No note with id {card.lookup_id} found in Anki, so it was not sent "
                "again; the next sync reconciles this card"
            )

    async def _read_adopted(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        """Read whole each adopted note its lookup read without cards (a content match)."""
        nids = sorted({c.nid for c in cards if c.nid is not None})
        if not nids:
            return
        try:
            held = await s.read_notes(nids)
        except Exception as e:
            held = dict.fromkeys(nids, e)
        for card in cards:
            got = held.get(card.nid or 0)
            if isinstance(got, HeldNote):
                card.held = got
            else:
                card.fail(f"reading adopted note {card.nid} failed: {got or 'not found'}")

    # -- compare and write ---------------------------------------------------

    async def _write(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        planned = await self._changes(s, cards)
        if self._dry:
            for card, change in planned:
                self._plan(_describe(card, change))
            return
        if planned:
            try:
                errors = await s.apply_changes([c for _, c in planned])
            except Exception as e:
                errors = [e] * len(planned)
            for (card, _), error in zip(planned, errors, strict=True):
                if error is not None:
                    card.fail(error)
        await self._read_created(s, [c for c in cards if c.how == "create" and c.error is None])

    async def _changes(
        self, s: NoteStoreSession, cards: list[_Card]
    ) -> list[tuple[_Card, NoteChange]]:
        """Each resolved note's change, its home deck made first; empty changes left out."""
        planned: list[tuple[_Card, NoteChange]] = []
        for card in cards:
            if card.error is None and card.held is not None and card.how != "create":
                change = self._change_for(card, card.held)
                if change is not None and not change.is_empty:
                    planned.append((card, change))
        moves = {c.home_deck[1] for _, c in planned if c.home_deck}
        failed = await self._ensure_decks(s, moves) if moves else {}
        for card, change in planned:
            if change.home_deck and change.home_deck[1] in failed:
                card.fail(failed[change.home_deck[1]])
        return [(card, change) for card, change in planned if card.error is None]

    def _change_for(self, card: _Card, held: HeldNote) -> NoteChange | None:
        """What the note needs to equal the vault, or None after failing the card."""
        note = card.work.note
        info = self._type(note.model)
        if not isinstance(info, NoteTypeInfo):
            card.fail(info)
            return None
        retype = None
        if held.model != info.name:
            n_cards = len(held.cards)
            keeps = n_cards if info.is_cloze else info.n_templates
            if n_cards > keeps:
                card.fail(
                    f"Changing note {held.nid} from {held.model} to {info.name} would delete "
                    f"{n_cards - keeps} of its {n_cards} cards and their reviews; not "
                    "converting. Split the card in the vault, or change the type in Anki."
                )
                return None
            old = self._types.get(held.model)
            old_fields = list(old.fields) if isinstance(old, NoteTypeInfo) else list(held.fields)
            retype = (info.name, tuple(map_note_type_fields(old_fields, list(info.fields))))
        fields = _type_fields(info, note.fields)
        if retype is None and all(held.fields.get(k) == v for k, v in fields.items()):
            fields = {}
        add, remove = _tag_change(note.tags, held.tags)
        return NoteChange(
            held.nid,
            retype=retype,
            fields=fields or None,
            add_tags=add,
            remove_tags=remove,
            home_deck=_deck_change(note.deck, held),
        )

    async def _read_created(self, s: NoteStoreSession, cards: list[_Card]) -> None:
        """Read the created notes once, for the card id the vault records."""
        nids = sorted({c.nid for c in cards if c.nid is not None})
        if not nids:
            return
        try:
            held = await s.read_notes(nids)
        except Exception as e:
            self._log.warning(f"Failed to fetch CIDs for {len(nids)} new notes: {e}")
            return
        for card in cards:
            got = held.get(card.nid or 0)
            if isinstance(got, HeldNote):
                card.held = got

    # -- report --------------------------------------------------------------

    def _result(self, card: _Card) -> UpdateItem:
        work = card.work
        if self._dry:
            return self._planned(card)
        if card.error is not None or card.nid is None:
            error = card.error or "no decision was reached for this card"
            self._log.error(f"ERR file={work.source_file} card={work.source_index} error={error}")
            return UpdateItem(
                work.source_file, work.source_index, None, None, False, error, work.note
            )
        cids = [c.cid for c in card.held.cards] if card.held else []
        new_cid = None
        if card.how != "update":
            new_cid = str(cids[0]) if cids else None
            self._log.info(
                f"[{'heal' if card.how == 'adopt' else 'create'}] {work.source_file} "
                f"#{work.source_index} -> nid={card.nid} cid={new_cid}"
            )
        elif work.note.cid and cids and work.note.cid.strip() not in {str(c) for c in cids}:
            new_cid = str(cids[0])
        return UpdateItem(
            work.source_file, work.source_index, str(card.nid), new_cid, True, None, work.note
        )

    def _planned(self, card: _Card) -> UpdateItem:
        """A dry run's result: the note the card resolved to, or the refusal; no cid."""
        work = card.work
        if card.error is not None:
            self._plan(f"would refuse {card.label}: {card.error}")
            return UpdateItem(
                work.source_file, work.source_index, None, None, False, card.error, work.note
            )
        if card.how == "adopt":
            self._plan(f"would adopt nid={card.nid} for {card.label}")
        nid = None if card.nid is None else str(card.nid)
        return UpdateItem(work.source_file, work.source_index, nid, None, True, None, work.note)


def _describe(card: _Card, change: NoteChange) -> str:
    """One line saying what a sync would overwrite on the card's note."""
    held = card.held
    parts = []
    if change.retype and held is not None:
        parts.append(f"type {held.model} -> {change.retype[0]}")
    if change.fields and held is not None:
        names = [k for k, v in change.fields.items() if held.fields.get(k) != v]
        parts.append(f"fields {', '.join(names or list(change.fields))}")
    if change.add_tags or change.remove_tags:
        tags = [f"+{t}" for t in change.add_tags] + [f"-{t}" for t in change.remove_tags]
        parts.append(f"tags {' '.join(tags)}")
    if change.home_deck and held is not None:
        before = sorted({str(c.home_deck) for c in held.cards})
        parts.append(f"deck {', '.join(before)} -> {change.home_deck[1]}")
    return f"would overwrite nid={change.nid} for {card.label}: {'; '.join(parts)}"


def _tag_change(
    vault: Sequence[str], held: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Tags to add and to remove so the note's tags equal the vault's, ignoring case.

    A tag Anki owns stays on the note.
    """
    wanted = {t.casefold() for t in vault}
    present = {t.casefold() for t in held}
    add = tuple(t for t in dict.fromkeys(vault) if t.casefold() not in present)
    remove = tuple(
        t for t in held if t.casefold() not in wanted and t.casefold() not in ANKI_OWNED_TAGS
    )
    return add, remove


def _deck_change(deck: str, held: HeldNote) -> tuple[tuple[int, ...], str] | None:
    """Every card filed under the vault's deck, unless each provably is already."""
    if not held.cards:
        return None
    if all(
        c.home_deck is not None and c.home_deck.casefold() == deck.casefold() for c in held.cards
    ):
        return None
    return tuple(c.cid for c in held.cards), deck
