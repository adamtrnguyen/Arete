from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from typing import Any

from arete.domain.constants import CHUNK_SIZE
from arete.domain.interfaces import AnkiBridge, NoteStoreSession
from arete.domain.models import (
    AddonOutdated,
    HeldCard,
    HeldNote,
    NewNote,
    NoteChange,
    NoteTypeInfo,
    OutcomeUnknown,
)
from arete.domain.ownership import is_arete_id
from arete.infrastructure.adapters.anki_connect_client import (
    DEFAULT_ANKI_CONNECT_URL,
    AnkiConnectClient,
)
from arete.infrastructure.anki.search import search_term

# Writes sent in one multi request. Every write resets Anki's UI on its main thread, so
# one request holds no more than a consumer batch.
_WRITES_PER_REQUEST = 50


def _outdated(action: str, error: object) -> AddonOutdated | None:
    """The error to stop the sync with, when the add-on answered that it lacks the action."""
    if isinstance(error, Exception) and "unsupported action" in str(error):
        return AddonOutdated(
            f"The Anki add-on lacks the '{action}' action the sync needs. Update the "
            "arete_ankiconnect add-on and restart Anki."
        )
    return None


class ConnectNoteSession(NoteStoreSession):
    """`NoteStoreSession` over AnkiConnect, batched: a few requests per call, any size.

    Reads go 500 ids per request; writes go in `multi` requests of at most
    `_WRITES_PER_REQUEST`, step by step (`apply_changes`). Note types come from
    `getModelInfo`, home decks from `getHomeDecks`, and a card is filed with `setHomeDeck`,
    which leaves a queued card in its filtered deck.

    An add-on that answers "unsupported action" to one of those (or `changeNoteType`) is
    too old for the sync and stops it with `AddonOutdated`.
    """

    def __init__(self, adapter: AnkiConnectAdapter):
        """Send through the adapter's `_invoke` and `_send_writes`."""
        self._a = adapter
        # The deck names, read along with the note types (one request for both).
        self._deck_names: set[str] | None = None

    # -- reads ---------------------------------------------------------------

    async def note_types(
        self, names: Collection[str]
    ) -> dict[str, NoteTypeInfo | None | Exception]:
        ordered = sorted(names)
        if not ordered:
            return {}
        info, decks = await self._a._send_writes(
            [("getModelInfo", {"models": ordered}), ("deckNames", {})], results=True
        )
        if isinstance(decks, list):
            self._deck_names = set(decks)
        if outdated := _outdated("getModelInfo", info):
            raise outdated
        if isinstance(info, Exception):
            return dict.fromkeys(ordered, info)
        return {name: _model_info(name, info.get(name)) for name in ordered}

    async def deck_names(self) -> set[str]:
        if self._deck_names is None:
            self._deck_names = set(await self._a._invoke("deckNames"))
        return set(self._deck_names)

    async def read_notes(self, nids: Sequence[int]) -> dict[int, HeldNote | None | Exception]:
        unique = list(dict.fromkeys(int(n) for n in nids))
        infos = await self._a._read_notes(unique)
        cids = [c for i in infos.values() if isinstance(i, dict) for c in i.get("cards") or []]
        homes = await self._homes_of(cids)
        out: dict[int, HeldNote | None | Exception] = {}
        for nid in unique:
            info = infos[nid]
            if isinstance(info, Exception):
                out[nid] = info
            elif not info.get("noteId"):
                out[nid] = None
            else:
                out[nid] = _held(info, homes)
        return out

    async def _homes_of(self, cids: list[int]) -> dict[int, tuple[str | None, bool | None]]:
        """Per card: its home deck and whether it is queued; a failed chunk stays unknown."""
        homes: dict[int, tuple[str | None, bool | None]] = {}
        for i in range(0, len(cids), CHUNK_SIZE):
            chunk = [int(c) for c in cids[i : i + CHUNK_SIZE]]
            try:
                reply = await self._a._invoke("getHomeDecks", cards=chunk)
                for cid, home in zip(chunk, reply, strict=True):
                    if home:
                        homes[cid] = (home.get("homeDeck"), bool(home.get("filteredDeck")))
            except Exception as e:
                if outdated := _outdated("getHomeDecks", e):
                    raise outdated from e
                self._a.logger.warning(f"[anki] getHomeDecks failed; decks unknown: {e}")
        return homes

    async def find_by_arete_ids(
        self, arete_ids: Sequence[str]
    ) -> dict[str, list[HeldNote] | Exception]:
        ids = list(dict.fromkeys(arete_ids))
        if not ids:
            return {}
        replies = await self._a._send_writes(
            [("findNotes", {"query": search_term("tag", i)}) for i in ids], results=True
        )
        hits = {i: r for i, r in zip(ids, replies, strict=True) if isinstance(r, list)}
        held = await self.read_notes(sorted({int(n) for found in hits.values() for n in found}))
        out: dict[str, list[HeldNote] | Exception] = {}
        for arete_id, reply in zip(ids, replies, strict=True):
            if arete_id not in hits:
                failed = reply if isinstance(reply, Exception) else None
                out[arete_id] = failed or Exception(f"findNotes answered {reply!r:.80}")
                continue
            out[arete_id] = _tagged(arete_id, sorted(int(n) for n in hits[arete_id]), held)
        return out

    async def scan_model(self, model: str) -> list[HeldNote]:
        """Every note of this type, and no note of another.

        Each hit's own type is checked against the name, ignoring case as Anki does: Anki
        keeps note type names unique ignoring case, and the direct backend finds the type
        the same way (`models.by_name`).
        """
        candidates = await self._a._invoke("findNotes", query=search_term("note", model))
        infos = await self._a._read_notes([int(n) for n in candidates or []])
        failed = [i for i in infos.values() if isinstance(i, Exception)]
        if failed:
            raise failed[0]
        wanted = model.casefold()
        return [
            _held(i, {})
            for i in infos.values()
            if isinstance(i, dict)
            and i.get("noteId")
            and str(i.get("modelName", "")).casefold() == wanted
        ]

    # -- writes --------------------------------------------------------------

    async def add_field(self, model: str, field: str) -> None:
        await self._a._invoke("modelFieldAdd", modelName=model, fieldName=field)

    async def create_decks(self, names: Collection[str]) -> dict[str, Exception | None]:
        ordered = sorted(names)
        if not ordered:
            return {}
        errors = await self._a._send_writes([("createDeck", {"deck": n}) for n in ordered])
        if self._deck_names is not None:
            self._deck_names.update(n for n, e in zip(ordered, errors, strict=True) if e is None)
        return dict(zip(ordered, errors, strict=True))

    async def create_notes(self, notes: Sequence[NewNote]) -> list[int | Exception]:
        out: list[int | Exception] = []
        for start in range(0, len(notes), _WRITES_PER_REQUEST):
            group = notes[start : start + _WRITES_PER_REQUEST]
            writes = [("addNote", {"note": _add_params(n)}) for n in group]
            results = await self._a._send_writes(writes, results=True)
            for result in results:
                if isinstance(result, Exception):
                    out.append(result)
                elif not result:
                    out.append(Exception("addNote returned null ID"))
                else:
                    out.append(int(result))
        return out

    async def apply_changes(self, changes: Sequence[NoteChange]) -> list[Exception | None]:
        """Each note's steps in order, the k-th step of every note in one request."""
        steps = [_steps_of(c) for c in changes]
        errors: list[Exception | None] = [None] * len(changes)
        k = 0
        while due := [i for i, s in enumerate(steps) if errors[i] is None and k < len(s)]:
            for start in range(0, len(due), _WRITES_PER_REQUEST):
                group = due[start : start + _WRITES_PER_REQUEST]
                sent = await self._a._send_writes([steps[i][k] for i in group])
                for i, error in zip(group, sent, strict=True):
                    errors[i] = await self._settle(changes[i], steps[i][k], error)
            k += 1
        return errors

    async def _settle(
        self, change: NoteChange, step: tuple[str, dict], error: Exception | None
    ) -> Exception | None:
        """A step's final error: an unknown action is named, a lost retype confirmed."""
        if error is None:
            return None
        action, _params = step
        if outdated := _outdated(action, error):
            raise outdated from error
        if action == "changeNoteType":
            return await self._confirm_retype(change, error)
        return error

    async def _confirm_retype(self, change: NoteChange, error: Exception) -> Exception | None:
        """A lost `changeNoteType` reply: done if the note has the new type, else the error.

        It is never sent again: its field map indexes the old type's fields.
        """
        if not isinstance(error, OutcomeUnknown) or change.retype is None:
            return error
        try:
            await self._a._confirm_retype(change.nid, change.retype[0], error)
        except OutcomeUnknown as still:
            return still
        return None


def _no_such_model(error: Exception) -> bool:
    """Whether the add-on refused because the collection holds no type of that name."""
    return "model was not found" in str(error)


def _model_info(name: str, info: Any) -> NoteTypeInfo | None:
    """A note type from `getModelInfo`: kind 1 is cloze; uses Anki's canonical name when sent."""
    if not isinstance(info, dict):
        return None
    fields = tuple(str(f) for f in info.get("fields") or [])
    canonical = str(info.get("name") or name)
    return NoteTypeInfo(canonical, fields, info.get("type") == 1, int(info.get("templates") or 0))


def _held(info: dict, homes: dict[int, tuple[str | None, bool | None]]) -> HeldNote:
    """A notesInfo entry as a held note; card order is Anki's `ord` order."""
    raw = info.get("fields") or {}
    names = sorted(raw, key=lambda n: (raw[n] or {}).get("order", 0))
    cards = tuple(
        HeldCard(int(c), i, *homes.get(int(c), (None, None)))
        for i, c in enumerate(info.get("cards") or [])
    )
    return HeldNote(
        int(info["noteId"]),
        str(info.get("modelName", "")),
        {n: str((raw[n] or {}).get("value", "")) for n in names},
        tuple(info.get("tags") or []),
        cards,
    )


def _tagged(
    arete_id: str, nids: list[int], held: dict[int, HeldNote | None | Exception]
) -> list[HeldNote] | Exception:
    """The notes among `nids` that carry the id as a whole tag, ignoring case."""
    key = arete_id.casefold()
    out: list[HeldNote] = []
    for nid in nids:
        note = held.get(nid)
        if isinstance(note, Exception):
            return note
        if note is not None and key in {t.casefold() for t in note.tags}:
            out.append(note)
    return out


def _add_params(new: NewNote) -> dict:
    """An addNote note. Anki's duplicate check is off: NoteSync refuses duplicates before."""
    return {
        "deckName": new.deck,
        "modelName": new.model,
        "fields": dict(new.fields),
        "tags": list(new.tags),
        "options": {"allowDuplicate": True},
    }


def _steps_of(change: NoteChange) -> list[tuple[str, dict]]:
    """The note's writes in the port's order: retype, fields, add tags, remove tags, deck."""
    nid = change.nid
    steps: list[tuple[str, dict]] = []
    if change.retype:
        model, mapping = change.retype
        steps.append(
            ("changeNoteType", {"notes": [nid], "modelName": model, "newFields": list(mapping)})
        )
    if change.fields:
        steps.append(("updateNoteFields", {"note": {"id": nid, "fields": dict(change.fields)}}))
    if change.add_tags:
        steps.append(("addTags", {"notes": [nid], "tags": " ".join(change.add_tags)}))
    if change.remove_tags:
        steps.append(("removeTags", {"notes": [nid], "tags": " ".join(change.remove_tags)}))
    if change.home_deck:
        cids, deck = change.home_deck
        steps.append(("setHomeDeck", {"cards": list(cids), "deck": deck}))
    return steps


class AnkiConnectAdapter(AnkiBridge):
    """Adapter for communicating with Anki via the AnkiConnect add-on (HTTP API)."""

    def __init__(
        self,
        url: str = DEFAULT_ANKI_CONNECT_URL,
        *,
        client: AnkiConnectClient | None = None,
    ):
        """Initialize with an AnkiConnect URL or shared `AnkiConnectClient`."""
        self.logger = logging.getLogger(__name__)
        self.client = client or AnkiConnectClient(url=url)

    @property
    def url(self) -> str:
        return self.client.url

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ConnectNoteSession]:
        """Note reads and writes over AnkiConnect (`NoteStorePort`); nothing to open."""
        yield ConnectNoteSession(self)

    async def is_responsive(self) -> bool:
        """Check if AnkiConnect is reachable and has the expected API version."""
        return await self.client.is_responsive()

    async def _read_notes(self, nids: list[int]) -> dict[int, dict | Exception]:
        """NotesInfo for each nid, one request per chunk; a failed chunk maps to its error.

        A nid Anki does not hold maps to ``{}``, as the add-on answers.
        """
        held: dict[int, dict | Exception] = {}
        unique = list(dict.fromkeys(nids))
        for i in range(0, len(unique), CHUNK_SIZE):
            chunk = unique[i : i + CHUNK_SIZE]
            try:
                infos = await self._invoke("notesInfo", notes=chunk)
                if not isinstance(infos, list) or len(infos) != len(chunk):
                    raise ValueError(f"notesInfo answered {infos!r:.80} for {len(chunk)} notes")
                held.update(zip(chunk, infos, strict=True))
            except Exception as e:
                held.update((nid, e) for nid in chunk)
        return held

    async def _send_writes(self, writes: list[tuple[str, dict]], results: bool = False) -> list:
        """Delegate batched writes to the shared AnkiConnect client."""
        return await self.client.send_writes(writes, results=results)

    async def _confirm_retype(self, nid: int, new_model: str, lost: OutcomeUnknown) -> None:
        """Count a ``changeNoteType`` with a lost reply as done if the note has the new type.

        It is never sent a second time: its field map indexes the old type's fields, and
        on a note already converted it would move them again. Otherwise this raises.
        """
        try:
            [held] = await self._invoke("notesInfo", notes=[nid])
        except Exception as e:
            raise OutcomeUnknown(f"{lost}. Re-reading note {nid} failed: {e}") from lost
        if str(held.get("modelName", "")).casefold() != new_model.casefold():
            raise OutcomeUnknown(
                f"{lost}. Note {nid} is still {held.get('modelName')!r}, so it was not sent "
                "again; the next sync converts it."
            ) from lost
        self.logger.warning(f"[retype] {lost}; note {nid} has type {new_model}, counted as done")

    async def _notes_info_chunked(self, nids: list[int]) -> list[dict]:
        """Fetch notesInfo in CHUNK_SIZE batches; a failed chunk is logged and skipped."""
        infos: list[dict] = []
        for i in range(0, len(nids), CHUNK_SIZE):
            try:
                infos.extend(await self._invoke("notesInfo", notes=nids[i : i + CHUNK_SIZE]))
            except Exception as e:
                self.logger.warning(f"Healing notesInfo failed: {e}")
        return infos

    async def _invoke(self, action: str, **params) -> Any:
        return await self.client.invoke(action, **params)

    async def get_deck_names(self) -> list[str]:
        return await self._invoke("deckNames")

    async def get_filtered_deck_names(self) -> set[str]:
        """Filtered (dynamic) deck names present in Anki."""
        decks = [d for d in await self.get_deck_names() if d.casefold() != "default"]
        if not decks:
            return set()
        replies = await self._send_writes(
            [("getDeckConfig", {"deck": d}) for d in decks], results=True
        )
        filtered: set[str] = set()
        for deck_name, cfg in zip(decks, replies, strict=True):
            if isinstance(cfg, Exception):
                raise RuntimeError(f"AnkiConnect error reading deck {deck_name!r}: {cfg}") from cfg
            if isinstance(cfg, dict) and bool(cfg.get("dyn")):
                filtered.add(deck_name)
        return filtered

    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Fetch NIDs of due cards, optionally including new cards."""
        deck_filter = f"{search_term('deck', deck_name)} " if deck_name else ""
        query = f"{deck_filter}(is:due)" if not include_new else f"{deck_filter}(is:due OR is:new)"

        # No fallback: an empty list here would build an empty queue and report
        # "nothing due" during an AnkiConnect outage. Let the caller fail loudly.
        return await self._invoke("findNotes", query=query)

    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """The first valid Arete ID tag of each note; a note without one is skipped.

        A failed read raises: an empty answer would build an empty queue and report
        nothing due.
        """
        arete_ids = []
        for i in range(0, len(nids), CHUNK_SIZE):
            for note in await self._invoke("notesInfo", notes=nids[i : i + CHUNK_SIZE]):
                tag = next((t for t in note.get("tags", []) if is_arete_id(t)), None)
                if tag:
                    arete_ids.append(tag)
        return arete_ids

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        """Every note in the deck, keyed by its note id as a string (as the direct backend)."""
        nids = await self._invoke("findNotes", query=search_term("deck", deck_name))
        return {str(nid): nid for nid in nids or []}

    async def get_note_tags(self, nids: list[int]) -> dict[int, list[str]]:
        """Tags per note. A note whose chunk failed is left out, so prune keeps it."""
        return {
            int(info["noteId"]): list(info.get("tags") or [])
            for info in await self._notes_info_chunked(nids)
            if info and "noteId" in info
        }

    async def delete_notes(self, nids: list[int]) -> bool:
        self.logger.info(f"Deleting notes: {nids}")
        await self._invoke("deleteNotes", notes=nids)
        return True

    async def delete_decks(self, names: list[str]) -> bool:
        await self._invoke("deleteDecks", decks=names, cardsToo=True)
        return True

    async def suspend_cards(self, cids: list[int]) -> bool:
        if not cids:
            return True  # Nothing to do
        await self._invoke("suspend", cards=cids)
        return True

    async def unsuspend_cards(self, cids: list[int]) -> bool:
        if not cids:
            return True
        await self._invoke("unsuspend", cards=cids)
        return True

    async def get_model_styling(self, model_name: str) -> str:
        """The note type's CSS; "" for a type Anki does not hold, as the direct backend."""
        try:
            res = await self._invoke("modelStyling", modelName=model_name)
        except Exception as e:
            if _no_such_model(e):
                return ""
            raise
        if isinstance(res, dict):
            return res.get("css", "")
        return str(res)

    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        """`{"Card 1": {"Front", "Back"}}`; {} for a type Anki does not hold."""
        try:
            return await self._invoke("modelTemplates", modelName=model_name)
        except Exception as e:
            if _no_such_model(e):
                return {}
            raise

    async def gui_browse(self, query: str) -> bool:
        """Open the Anki browser via AnkiConnect's guiBrowse action."""
        try:
            await self._invoke("guiBrowse", query=query)
            return True
        except Exception as e:
            self.logger.error(f"Failed to open Anki browser: {e}")
            return False

    async def get_card_ids_for_arete_ids(self, arete_ids: list[str]) -> list[int]:
        """Resolve Arete IDs to CIDs, preserving topological input order.

        A failed lookup raises: dropping its cards would build a shorter queue, or none,
        with "Have you synced?" as the reason.
        """
        cids_ordered: list[int] = []
        seen: set[int] = set()
        CHUNK = 50
        for start in range(0, len(arete_ids), CHUNK):
            chunk = arete_ids[start : start + CHUNK]
            actions = [
                {
                    "action": "findCards",
                    "version": 6,
                    "params": {"query": search_term("tag", aid)},
                }
                for aid in chunk
            ]
            results = await self._invoke("multi", actions=actions)
            for aid, reply in zip(chunk, results, strict=True):
                if isinstance(reply, dict) and set(reply) == {"result", "error"}:
                    if reply["error"] is not None:
                        err = reply["error"]
                        raise RuntimeError(f"looking up the cards of {aid} failed: {err}")
                    cids = reply["result"]
                elif isinstance(reply, list):
                    cids = reply
                else:
                    error = reply.get("error") if isinstance(reply, dict) else reply
                    raise RuntimeError(f"looking up the cards of {aid} failed: {error}")
                if not isinstance(cids, list):
                    raise RuntimeError(f"looking up the cards of {aid} failed: {cids}")
                for cid in cids:
                    if cid not in seen:
                        seen.add(cid)
                        cids_ordered.append(cid)
        return cids_ordered

    async def create_topo_deck(
        self, deck_name: str, cids: list[int], reschedule: bool = True
    ) -> bool:
        """Create a filtered deck via the Arete AnkiConnect plugin."""
        if not cids:
            return False
        try:
            await self._invoke(
                "createFilteredDeck",
                name=deck_name,
                cids=cids,
                reschedule=reschedule,
            )
            return True
        except Exception as e:
            if outdated := _outdated("createFilteredDeck", e):
                raise outdated from e
            self.logger.error(f"create_topo_deck failed: {e}")
            return False

    async def close(self) -> None:
        await self.client.close()
