import asyncio
import json
import logging
import platform
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from arete.domain.constants import (
    CHUNK_SIZE,
    FSRS_DIFFICULTY_SCALE,
    REQUEST_TIMEOUT,
    RESPONSIVENESS_TIMEOUT,
    SYNC_CONCURRENCY,
)
from arete.domain.interfaces import AnkiBridge
from arete.domain.models import AnkiCardStats, AnkiDeck, UpdateItem, WorkItem
from arete.domain.note_types import map_note_type_fields

# Writes sent in one multi request. Every write resets Anki's UI on its main thread, so
# one request holds no more than a consumer batch.
_WRITES_PER_REQUEST = 50

# getDecks names the Default deck for a card whose deck id no longer exists, so a card
# reported there is no proof that a changeDeck would change nothing.
_UNPROVABLE_DECK = "Default"


@dataclass
class _NotePlan:
    """One work item of a sync_notes batch: its result and the writes it still needs.

    ``nid``/``info`` are set when the item updates an existing note: the target and its
    notesInfo entry as read before any write of the batch. ``label`` is ``update``,
    ``heal`` or ``create``; a healed or created note gets its cid after the writes.
    """

    item: WorkItem
    result: UpdateItem
    label: str = "update"
    nid: int | None = None
    info: dict = field(default_factory=dict)
    retyped: bool = False
    shared: bool = False  # another item of the batch updates the same note
    writes: list[tuple[str, dict]] = field(default_factory=list)


class AnkiConnectAdapter(AnkiBridge):
    """Adapter for communicating with Anki via the AnkiConnect add-on (HTTP API)."""

    def __init__(self, url: str = "http://127.0.0.1:8765"):
        """Initialize with AnkiConnect URL, auto-detecting WSL bridge if needed."""
        self.logger = logging.getLogger(__name__)
        self._known_decks = set()
        self._source_field_ok: set[str] = set()
        # Per-run reconcile index: model -> normalized first field -> [nid, ...] (sorted).
        # Built lazily, collection-wide, the first time a card without a usable nid shows up.
        self._content_index: dict[str, dict[str, list[int]]] = {}
        # The build in flight per model, shared by every card that asks while it runs.
        self._index_builds: dict[str, asyncio.Future[dict[str, list[int]]]] = {}
        # Deck creations and source-field checks in flight, shared by concurrent batches.
        self._deck_flights: dict[str, asyncio.Future[dict[str, bool]]] = {}
        self._field_flights: dict[str, asyncio.Future[bool]] = {}
        self.use_windows_curl = False
        self._client: httpx.AsyncClient | None = None
        self._invoke_sem = asyncio.Semaphore(SYNC_CONCURRENCY)

        # The URL comes from config.anki_connect_url (CLI flag, config.toml, or the
        # ARETE_ANKI_CONNECT_URL env var). No second env var is read here.

        # WSL Logic
        if "microsoft" in platform.uname().release.lower():
            # Strategy A: curl.exe bridge (Preferred for 127.0.0.1)
            curl_path = shutil.which("curl.exe")
            if curl_path:
                self.use_windows_curl = True
                if "127.0.0.1" in url or "localhost" in url:
                    url = url.replace("localhost", "127.0.0.1")
                self.logger.info(
                    f"WSL detected: Using curl.exe bridge (found at {curl_path}) to talk to {url}"
                )
                self.url = url
                return
            else:
                self.logger.debug("WSL detected but 'curl.exe' not found in PATH.")

            # Strategy B: /etc/resolv.conf (Fallback)
            if "localhost" in url or "127.0.0.1" in url:
                try:
                    with open("/etc/resolv.conf") as f:
                        for line in f:
                            if line.startswith("nameserver"):
                                host_ip = line.split()[1].strip()
                                url = url.replace("localhost", host_ip).replace(
                                    "127.0.0.1", host_ip
                                )
                                self.logger.info(
                                    f"WSL detected: Auto-corrected URL using resolv.conf to http://{host_ip}:8765"
                                )
                                break
                except Exception as e:
                    self.logger.warning(f"WSL detected but failed to find host IP: {e}")

        self.url = url
        self.logger.debug(
            f"AnkiConnectAdapter initialized with url={self.url} "
            f"(curl_bridge={self.use_windows_curl})"
        )

    async def is_responsive(self) -> bool:
        """Check if AnkiConnect is reachable and has the expected API version."""
        try:
            payload = {"action": "version", "version": 6}
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
            resp = await self._client.post(self.url, json=payload, timeout=RESPONSIVENESS_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return int(data.get("result", 0)) >= 6
            return False
        except Exception:
            return False

    async def ensure_deck(self, deck: AnkiDeck | str) -> bool:
        name = deck.name if isinstance(deck, AnkiDeck) else deck
        return (await self._ensure_decks({name}))[name]

    async def _ensure_decks(self, names: set[str]) -> dict[str, bool]:
        """CreateDeck every name not yet known, all in one request; True where it exists.

        A name already being created by a concurrent caller is awaited, not sent again.
        """
        todo = sorted(
            n for n in names if n not in self._known_decks and n not in self._deck_flights
        )
        if todo:
            flight = asyncio.ensure_future(self._create_decks(todo))
            for name in todo:
                self._deck_flights[name] = flight

            def _land(_: object, landed: tuple[str, ...] = tuple(todo)) -> None:
                for name in landed:
                    self._deck_flights.pop(name, None)

            flight.add_done_callback(_land)

        ok: dict[str, bool] = {}
        for name in names:
            flight = self._deck_flights.get(name)
            if name in self._known_decks or flight is None:
                ok[name] = name in self._known_decks
            else:
                ok[name] = (await asyncio.shield(flight)).get(name, False)
        return ok

    async def _create_decks(self, names: list[str]) -> dict[str, bool]:
        errors = await self._send_writes([("createDeck", {"deck": name}) for name in names])
        for name, error in zip(names, errors, strict=True):
            if error is None:
                self._known_decks.add(name)
            else:
                self.logger.error(f"Failed to ensure deck '{name}': {error}")
        return {name: error is None for name, error in zip(names, errors, strict=True)}

    async def ensure_model_has_source_field(self, model_name: str) -> bool:
        """Add the _obsidian_source field to the note model if it lacks one.

        A check already running for the model, from a concurrent caller, is awaited.
        """
        if model_name in self._source_field_ok:
            return True
        flight = self._field_flights.get(model_name)
        if flight is None:
            flight = asyncio.ensure_future(self._add_source_field(model_name))
            self._field_flights[model_name] = flight
            flight.add_done_callback(lambda _, m=model_name: self._field_flights.pop(m, None))
        return await asyncio.shield(flight)

    async def _add_source_field(self, model_name: str) -> bool:
        try:
            # Get current model fields
            fields = await self._invoke("modelFieldNames", modelName=model_name)
            if "_obsidian_source" not in fields:
                # Add the field to the model
                await self._invoke(
                    "modelFieldAdd",
                    modelName=model_name,
                    fieldName="_obsidian_source",
                )
                self.logger.info(f"Added '_obsidian_source' field to model '{model_name}'")

            self._source_field_ok.add(model_name)
            return True
        except Exception as e:
            self.logger.warning(f"Could not add _obsidian_source field to '{model_name}': {e}")
            return False

    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        # Batch preparation: the batch's decks in one request, its note types concurrently.
        # A failure is logged here; the items it affects fail on their own writes.
        unique_models = {item.note.model for item in work_items}
        decks_ok, _ = await asyncio.gather(
            self._ensure_decks({item.note.deck for item in work_items}),
            asyncio.gather(*(self.ensure_model_has_source_field(m) for m in unique_models)),
        )
        for deck_name, ok in decks_ok.items():
            if not ok:
                self.logger.warning(f"Failed to ensure deck '{deck_name}' for batch")

        # The held notes are read in one notesInfo per chunk. Each item then resolves its
        # target (reconcile lookups, note-type change, addNote) concurrently, and the
        # updates' writes go out together, the k-th write of every note in one request.
        held = await self._read_notes(
            [nid for i in work_items if (nid := self._nid_of(i.note)) is not None]
        )
        sem = asyncio.Semaphore(SYNC_CONCURRENCY)

        async def _bounded(item: WorkItem) -> _NotePlan:
            async with sem:
                return await self._plan_note(item, held)

        plans = list(await asyncio.gather(*(_bounded(item) for item in work_items)))

        targets = Counter(p.nid for p in plans if p.nid is not None)
        for plan in plans:
            if plan.nid is not None:
                plan.shared = targets[plan.nid] > 1
                plan.writes = self._writes_for(plan)
        await self._drop_moves_already_done(plans)
        await self._run_writes(plans)
        await self._fill_first_cids([p for p in plans if p.label != "update" and p.result.ok])
        return [plan.result for plan in plans]

    @staticmethod
    def _nid_of(note: Any) -> int | None:
        """The note id a card carries, or None; a malformed one fails in _plan_note."""
        try:
            return int(note.nid) if note.nid else None
        except (TypeError, ValueError):
            return None

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

    def _failed(self, item: WorkItem, error: Exception) -> UpdateItem:
        self.logger.error(f"ERR file={item.source_file} card={item.source_index} error={error}")
        return UpdateItem(
            source_file=item.source_file,
            source_index=item.source_index,
            new_nid=None,
            new_cid=None,
            ok=False,
            error=str(error),
            note=item.note,
        )

    async def _plan_note(self, item: WorkItem, held: dict[int, dict | Exception]) -> _NotePlan:
        """Resolve one work item to the note it updates, or create it; writes come later."""
        note = item.note
        try:
            html_fields = dict(note.fields)

            if note.nid:
                info = held[int(note.nid)]
                if isinstance(info, Exception):
                    raise info
                if info.get("noteId"):
                    return await self._plan_update(item, int(note.nid), info, "update")
                self.logger.warning(
                    f"[dangling-nid] {item.source_file.name} #{item.source_index}: "
                    f"nid={note.nid} not in Anki; reconciling by ID tag / content"
                )

            return await self._add_or_heal_note(item, note, html_fields)

        except Exception as e:
            return _NotePlan(item=item, result=self._failed(item, e))

    async def _plan_update(self, item: WorkItem, nid: int, info: dict, label: str) -> _NotePlan:
        """Plan the update of an existing note; a note-type change is done here, first."""
        note = item.note
        retyped = info.get("modelName") not in (None, note.model)
        if retyped:
            await self._change_note_type(nid, info, note.model)
        self.logger.debug(f"[{label}] {item.source_file} #{item.source_index} -> nid={nid}")
        result = UpdateItem(
            source_file=item.source_file,
            source_index=item.source_index,
            new_nid=str(nid),
            new_cid=None,
            ok=True,
            note=note,
        )
        return _NotePlan(item=item, result=result, label=label, nid=nid, info=info, retyped=retyped)

    def _writes_for(self, plan: _NotePlan) -> list[tuple[str, dict]]:
        """The ordered writes that bring the note to the vault's fields, tags and deck.

        ``updateNoteFields`` is left out only when every field the note type has already
        holds exactly the string that would be sent (the add-on ignores a field the type
        lacks). A shared or retyped plan sends it regardless: another write of the batch,
        or the type change, makes ``info`` stale.
        """
        note, nid, info = plan.item.note, plan.nid, plan.info
        writes: list[tuple[str, dict]] = []
        fields = dict(note.fields)
        if plan.shared or plan.retyped or self._fields_differ(info, fields):
            writes.append(("updateNoteFields", {"note": {"id": nid, "fields": fields}}))

        if "tags" in info:
            current_tags = set(info["tags"])
            new_tags = set(note.tags)
            to_add = list(new_tags - current_tags)
            to_remove = list(current_tags - new_tags)
            if to_add:
                writes.append(("addTags", {"notes": [nid], "tags": " ".join(to_add)}))
            if to_remove:
                writes.append(("removeTags", {"notes": [nid], "tags": " ".join(to_remove)}))

        if "cards" in info:
            writes.append(("changeDeck", {"cards": info["cards"], "deck": note.deck}))
        else:
            self.logger.warning(
                f"[anki] Cannot move cards for nid={nid}. Info missing cards: {info}"
            )
        return writes

    @staticmethod
    def _fields_differ(info: dict, fields: dict) -> bool:
        """True unless each sent field the note has already holds exactly that value."""
        held = info.get("fields")
        if not isinstance(held, dict):
            return True
        for name, value in fields.items():
            if name not in held:
                continue  # updateNoteFields skips a field the note type lacks
            entry = held[name]
            if not isinstance(entry, dict) or entry.get("value") != value:
                return True
        return False

    async def _drop_moves_already_done(self, plans: list[_NotePlan]) -> None:
        """Leave out changeDeck where getDecks shows every card already in the target deck.

        A card in a filtered deck is reported there, so it is still moved home as before.
        A plan whose ``info`` predates a note-type change, or that shares its note with
        another item, keeps its move. When getDecks fails the moves are all sent.
        """
        checks: list[tuple[_NotePlan, list[int]]] = []
        for plan in plans:
            move = next((p for a, p in plan.writes if a == "changeDeck"), None)
            if plan.shared or plan.retyped or not move or not move["cards"]:
                continue
            if move["deck"] != _UNPROVABLE_DECK:
                checks.append((plan, [int(c) for c in move["cards"]]))
        if not checks:
            return
        cids = list(dict.fromkeys(c for _, cards in checks for c in cards))
        deck_of: dict[int, str] = {}
        for i in range(0, len(cids), CHUNK_SIZE):
            try:
                reply = await self._invoke("getDecks", cards=cids[i : i + CHUNK_SIZE])
                for deck_name, deck_cids in reply.items():
                    deck_of.update((int(c), deck_name) for c in deck_cids)
            except Exception as e:
                self.logger.warning(f"[anki] getDecks failed; moving those cards anyway: {e}")
        for plan, cards in checks:
            deck = plan.item.note.deck
            if all(deck_of.get(c) == deck for c in cards):
                plan.writes = [w for w in plan.writes if w[0] != "changeDeck"]

    async def _run_writes(self, plans: list[_NotePlan]) -> None:
        """Send each note's writes in order, the k-th write of every note in one request.

        A note whose write fails is marked failed and gets none of its later writes, as if
        its writes had been sent one by one; other notes are not affected.
        """
        step = 0
        while due := [p for p in plans if p.result.ok and step < len(p.writes)]:
            for start in range(0, len(due), _WRITES_PER_REQUEST):
                group = due[start : start + _WRITES_PER_REQUEST]
                errors = await self._send_writes([p.writes[step] for p in group])
                for plan, error in zip(group, errors, strict=True):
                    if error is not None:
                        plan.result = self._failed(plan.item, error)
            step += 1

    async def _send_writes(self, writes: list[tuple[str, dict]]) -> list[Exception | None]:
        """Send writes in one request, a lone one as itself, and return each one's error."""
        if len(writes) == 1:
            action, params = writes[0]
            try:
                await self._invoke(action, **params)
                return [None]
            except Exception as e:
                return [e]
        actions = [{"action": a, "version": 6, "params": p} for a, p in writes]
        try:
            replies = await self._invoke("multi", actions=actions)
            if not isinstance(replies, list) or len(replies) != len(writes):
                raise ValueError(f"multi answered {replies!r:.80} for {len(writes)} actions")
        except Exception as e:
            return [e] * len(writes)
        errors: list[Exception | None] = []
        for reply in replies:
            if not isinstance(reply, dict) or set(reply) != {"result", "error"}:
                errors.append(ValueError(f"multi sub-reply is malformed: {reply!r:.80}"))
            elif reply["error"] is not None:
                errors.append(Exception(reply["error"]))
            else:
                errors.append(None)
        return errors

    async def _fill_first_cids(self, plans: list[_NotePlan]) -> None:
        """Set ``new_cid`` on created and healed notes from one notesInfo per chunk.

        It runs after every write of the batch, so the cid is the note's first card as
        Anki holds it then. A note whose notesInfo failed keeps ``new_cid=None``.
        """
        if not plans:
            return
        nids = list(dict.fromkeys(int(p.result.new_nid or 0) for p in plans))
        first_cid: dict[int, str] = {}
        for info in await self._notes_info_chunked(nids):
            if info and info.get("noteId") and info.get("cards"):
                first_cid[int(info["noteId"])] = str(info["cards"][0])
        for plan in plans:
            result = plan.result
            nid = int(result.new_nid or 0)
            result.new_cid = first_cid.get(nid)
            if result.new_cid is None:
                self.logger.warning(f"Failed to fetch CID for nid={nid}")
            deck = f" deck={plan.item.note.deck!r}" if plan.label == "heal" else ""
            self.logger.info(
                f"[{plan.label}] {result.source_file} #{result.source_index} -> nid={nid} "
                f"cid={result.new_cid}{deck}"
            )

    async def _change_note_type(self, nid: int, info: dict, new_model: str) -> None:
        """Convert a note in place with the add-on's changeNoteType; reviews are kept."""
        n_cards = len(info.get("cards") or [])
        if new_model != "Cloze":
            keeps = len(await self._invoke("modelTemplates", modelName=new_model) or {})
            if n_cards > keeps:
                raise RuntimeError(
                    f"Changing note {nid} from {info.get('modelName')} to {new_model} would "
                    f"delete {n_cards - keeps} of its {n_cards} cards and their reviews; not "
                    "converting. Split the card in the vault, or change the type in Anki."
                )
        fields = info.get("fields", {})
        old_fields = sorted(fields, key=lambda name: fields[name].get("order", 0))
        new_fields = await self._invoke("modelFieldNames", modelName=new_model)
        await self._invoke(
            "changeNoteType",
            notes=[nid],
            modelName=new_model,
            newFields=map_note_type_fields(old_fields, new_fields),
        )
        self.logger.info(f"[anki] note {nid}: note type {info.get('modelName')} -> {new_model}")

    async def _add_or_heal_note(self, item: WorkItem, note: Any, html_fields: dict) -> _NotePlan:
        """Add a new note or heal by matching existing content."""
        existing_nid, existing_info = await self._find_existing_note(note, html_fields)

        if existing_nid:
            # Reconcile: same treatment as a note with a valid nid -- fields, tags AND deck.
            # A twin that landed in the wrong deck is moved, never duplicated.
            info = existing_info or await self._invoke("notesInfo", notes=[existing_nid])
            return await self._plan_update(item, existing_nid, info[0] if info else {}, "heal")

        params = {
            "note": {
                "deckName": note.deck,
                "modelName": note.model,
                "fields": html_fields,
                "tags": note.tags,
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            }
        }
        new_id = await self._invoke("addNote", **params)
        if not new_id:
            raise Exception("addNote returned null ID")
        self._index_new_note(note, html_fields, int(new_id))

        result = UpdateItem(
            source_file=item.source_file,
            source_index=item.source_index,
            new_nid=str(new_id),
            new_cid=None,
            ok=True,
            note=note,
        )
        return _NotePlan(item=item, result=result, label="create")

    @staticmethod
    def _normalize_field(value: str) -> str:
        """Normalize a field value for comparison: strip HTML, cloze markers, whitespace."""
        text = re.sub(r"<[^>]+>", "", value)
        text = re.sub(r"\{\{c\d+::", "", text).replace("}}", "")
        return " ".join(text.split()).strip().lower()

    async def _find_existing_note(
        self, note: Any, html_fields: dict
    ) -> tuple[int | None, list[dict] | None]:
        """Find the Anki note that already IS this card, for a card without a usable nid.

        Lookup order, both collection-wide:
          1. the card's ``arete_`` ID tag (exact identity);
          2. normalized first-field content within the same model (indexed once per run).
        Deck is deliberately not part of either lookup: a twin that landed in the wrong
        deck is still the same card and gets moved by the caller, not duplicated.
        Returns the nid and, when the lookup already read it, the note's notesInfo reply.
        """
        first_field_name = next(iter(html_fields))
        our_value = self._normalize_field(html_fields[first_field_name])
        if not our_value:
            return None, None

        arete_id = next((t for t in note.tags if str(t).startswith("arete_")), None)
        if arete_id:
            match = await self._match_by_tag(arete_id, first_field_name, our_value)
            if match is not None:
                self.logger.info(f" -> Healed by arete ID {arete_id}: nid={match['noteId']}")
                return int(match["noteId"]), [match]

        index = await self._content_index_for(note.model, first_field_name)
        matches = index.get(our_value)
        if matches:
            nid = matches[0]
            self.logger.info(f" -> Healed! matched existing note: {nid}")
            return nid, None
        return None, None

    async def _match_by_tag(
        self, arete_id: str, first_field_name: str, our_value: str
    ) -> dict | None:
        """Resolve a card by its arete ID tag; content must still match (tags can be copied).

        Returns the notesInfo entry of the matched note.
        """
        try:
            candidates = await self._invoke("findNotes", query=f"tag:{arete_id}")
        except Exception as e:
            self.logger.warning(f"Healing tag query failed: {e}")
            return None
        if not candidates:
            return None

        matches = {
            int(info["noteId"]): info
            for info in await self._notes_info_chunked(candidates)
            if self._normalize_field(
                info.get("fields", {}).get(first_field_name, {}).get("value", "")
            )
            == our_value
        }
        if not matches:
            self.logger.warning(
                f"[heal] tag {arete_id} is on {len(candidates)} note(s) but none match "
                f"content; falling back to content lookup"
            )
            return None
        if len(matches) > 1:
            self.logger.warning(
                f"[heal] tag {arete_id} matches {len(matches)} notes; using oldest "
                f"nid={min(matches)} (the rest are duplicates -- prune them)"
            )
        return matches[min(matches)]

    async def _content_index_for(self, model: str, first_field_name: str) -> dict[str, list[int]]:
        """Build (once per run, per model) normalized first field -> sorted nids.

        Callers that arrive while a build runs await that build instead of starting their
        own. A failed build is shared by its waiters but not cached; the next caller retries.
        """
        cached = self._content_index.get(model)
        if cached is not None:
            return cached
        build = self._index_builds.get(model)
        if build is None:
            build = asyncio.ensure_future(self._build_content_index(model, first_field_name))
            self._index_builds[model] = build
            build.add_done_callback(lambda _, m=model: self._index_builds.pop(m, None))
        # shield: one cancelled caller must not cancel the build the others wait on
        return await asyncio.shield(build)

    async def _build_content_index(self, model: str, first_field_name: str) -> dict[str, list[int]]:
        try:
            candidates = await self._invoke("findNotes", query=f'"note:{model}"')
        except Exception as e:
            self.logger.warning(f"Healing query failed: {e}")
            return {}  # do not cache a failed build

        index: dict[str, list[int]] = {}
        for info in await self._notes_info_chunked(candidates or []):
            val = self._normalize_field(
                info.get("fields", {}).get(first_field_name, {}).get("value", "")
            )
            if val:
                index.setdefault(val, []).append(int(info["noteId"]))
        for nids in index.values():
            nids.sort()
        self._content_index[model] = index
        return index

    async def _notes_info_chunked(self, nids: list[int]) -> list[dict]:
        """Fetch notesInfo in CHUNK_SIZE batches; a failed chunk is logged and skipped."""
        infos: list[dict] = []
        for i in range(0, len(nids), CHUNK_SIZE):
            try:
                infos.extend(await self._invoke("notesInfo", notes=nids[i : i + CHUNK_SIZE]))
            except Exception as e:
                self.logger.warning(f"Healing notesInfo failed: {e}")
        return infos

    def _index_new_note(self, note: Any, html_fields: dict, nid: int) -> None:
        """Register a just-created note so a second identical card this run heals to it."""
        index = self._content_index.get(note.model)
        if index is None:
            return
        first_field_name = next(iter(html_fields))
        val = self._normalize_field(html_fields[first_field_name])
        if val:
            index.setdefault(val, []).append(nid)

    async def _invoke(self, action: str, **params) -> Any:
        payload = {"action": action, "version": 6, "params": params}
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with self._invoke_sem:
                    data = await asyncio.wait_for(
                        self._http_request(payload), timeout=REQUEST_TIMEOUT
                    )

                if len(data) != 2:
                    raise ValueError("response has an unexpected number of fields")
                if "error" not in data:
                    raise ValueError("response is missing required error field")
                if "result" not in data:
                    raise ValueError("response is missing required result field")
                if data["error"] is not None:
                    raise Exception(data["error"])
                return data["result"]
            except TimeoutError as exc:
                self.logger.warning(
                    f"AnkiConnect timeout on '{action}' (attempt {attempt + 1}/{max_retries + 1})"
                )
                # Reset the HTTP client to drop stale connections
                if self._client is not None:
                    await self._client.aclose()
                    self._client = None
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                raise TimeoutError(
                    f"AnkiConnect timeout on '{action}' after {max_retries + 1} attempts"
                ) from exc
            except Exception as e:
                self.logger.error(f"AnkiConnect call failed: {e}")
                raise

    async def _http_request(self, payload: dict) -> dict:
        if self.use_windows_curl:
            cmd = ["curl.exe", "-s", "-X", "POST", self.url, "-d", "@-"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin_data = json.dumps(payload).encode("utf-8")
            stdout, stderr = await proc.communicate(input=stdin_data)
            if proc.returncode != 0:
                raise Exception(f"curl.exe failed: {stderr.decode('utf-8')}")
            return json.loads(stdout.decode("utf-8"))
        else:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT,
                    limits=httpx.Limits(
                        max_connections=SYNC_CONCURRENCY,
                        max_keepalive_connections=SYNC_CONCURRENCY,
                    ),
                )
            resp = await self._client.post(self.url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_deck_names(self) -> list[str]:
        return await self._invoke("deckNames")

    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Fetch NIDs of due cards, optionally including new cards."""
        deck_filter = f'deck:"{deck_name}" ' if deck_name else ""
        query = f"{deck_filter}(is:due)" if not include_new else f"{deck_filter}(is:due OR is:new)"

        # No fallback: an empty list here would build an empty queue and report
        # "nothing due" during an AnkiConnect outage. Let the caller fail loudly.
        return await self._invoke("findNotes", query=query)

    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """Convert NIDs to Arete IDs via tags; empty if any notesInfo chunk fails."""
        if not nids:
            return []

        arete_ids = []
        try:
            infos: list[dict] = []
            for i in range(0, len(nids), CHUNK_SIZE):
                infos.extend(await self._invoke("notesInfo", notes=nids[i : i + CHUNK_SIZE]))
            for note in infos:
                tags = note.get("tags", [])
                for tag in tags:
                    if tag.startswith("arete_"):
                        arete_ids.append(tag)
                        break
        except Exception as e:
            self.logger.error(f"Failed to map nids {nids} to arete ids: {e}")

        return arete_ids

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        """Every note in the deck, keyed by its note id as a string (as the direct backend)."""
        nids = await self._invoke("findNotes", query=f'"deck:{deck_name}"')
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

    async def get_learning_insights(self, lapse_threshold: int = 3) -> Any:
        from arete.domain.constants import MAX_PROBLEMATIC_NOTES
        from arete.domain.stats.models import LearningStats, NoteInsight

        # Find leech cards directly via AnkiConnect
        leech_cids = await self._invoke("findCards", query=f"prop:lapses>={lapse_threshold}")
        all_cids = await self._invoke("findCards", query="")

        if not leech_cids:
            return LearningStats(total_cards=len(all_cids))

        infos = await self._invoke("cardsInfo", cards=leech_cids)

        # Aggregate by note: keep max lapses per note
        nid_map: dict[int, dict] = {}
        for info in infos:
            nid = info.get("note")
            lapses = info.get("lapses", 0)
            if nid not in nid_map or lapses > nid_map[nid]["lapses"]:
                fields = info.get("fields", {})
                note_name = "Unknown"
                if "_obsidian_source" in fields:
                    note_name = fields["_obsidian_source"].get("value", "Unknown")
                elif fields:
                    first_key = next(iter(fields))
                    note_name = fields[first_key].get("value", "Unknown")
                # Strip HTML
                import re

                note_name = re.sub("<[^<]+?>", "", note_name).strip()

                nid_map[nid] = {
                    "note_name": note_name,
                    "lapses": lapses,
                    "deck": info.get("deckName", "Unknown"),
                }

        problematic = sorted(nid_map.values(), key=lambda x: x["lapses"], reverse=True)
        notes = [
            NoteInsight(
                note_name=n["note_name"],
                issue=f"{n['lapses']} lapses",
                lapses=n["lapses"],
                deck=n["deck"],
            )
            for n in problematic[:MAX_PROBLEMATIC_NOTES]
        ]

        return LearningStats(total_cards=len(all_cids), problematic_notes=notes)

    async def get_card_stats(self, nids: list[int]) -> list[AnkiCardStats]:
        """Fetch stats via AnkiConnect."""
        if not nids:
            return []

        all_stats: list[AnkiCardStats] = []
        for i in range(0, len(nids), CHUNK_SIZE):
            chunk = nids[i : i + CHUNK_SIZE]
            try:
                chunk_stats = await self._fetch_stats_chunk(chunk)
                all_stats.extend(chunk_stats)
            except Exception as e:
                self.logger.error(f"Failed to fetch card stats chunk: {e}")
        return all_stats

    async def _fetch_stats_chunk(self, chunk: list[int]) -> list[AnkiCardStats]:
        """Fetch card stats for a single chunk of NIDs."""
        query = " OR ".join([f"nid:{n}" for n in chunk])
        card_ids = await self._invoke("findCards", query=query)
        if not card_ids:
            return []

        infos = await self._invoke("cardsInfo", cards=card_ids)
        fsrs_map = await self._fetch_fsrs_map(card_ids)

        return [self._build_card_stat(info, fsrs_map) for info in infos]

    async def _fetch_fsrs_map(self, card_ids: list[int]) -> dict[int, float]:
        """Fetch FSRS difficulty scores, returning empty dict on failure."""
        try:
            fsrs_results = await self._invoke("getFSRSStats", cards=card_ids)
            if not fsrs_results or not isinstance(fsrs_results, list):
                return {}
            return {
                item["cardId"]: item["difficulty"] / FSRS_DIFFICULTY_SCALE
                for item in fsrs_results
                if "cardId" in item and "difficulty" in item and item["difficulty"] is not None
            }
        except Exception as e:
            self.logger.warning(f"[fsrs] getFSRSStats unavailable, difficulties empty: {e}")
            return {}

    @staticmethod
    def _build_card_stat(info: dict[str, Any], fsrs_map: dict[int, float]) -> AnkiCardStats:
        """Build an AnkiCardStats from a single cardsInfo entry."""
        cid = info.get("cardId", 0)
        difficulty = fsrs_map.get(cid)
        if difficulty is None and info.get("difficulty") is not None:
            # S1: same 0-1 scale as the getFSRSStats path above (raw is Anki's 1-10).
            difficulty = info["difficulty"] / FSRS_DIFFICULTY_SCALE

        front = None
        fields = info.get("fields", {})
        if fields:
            first_key = next(iter(fields))
            front = fields[first_key].get("value")

        return AnkiCardStats(
            card_id=cid,
            note_id=info.get("note", 0),
            lapses=info.get("lapses", 0),
            ease=info.get("factor", 0),
            difficulty=difficulty,
            deck_name=info.get("deckName", "Unknown"),
            interval=info.get("interval", 0),
            due=info.get("due", 0),
            reps=info.get("reps", 0),
            average_time=0,
            front=front,
        )

    async def suspend_cards(self, cids: list[int]) -> bool:
        if not cids:
            return True  # Nothing to do
        res = await self._invoke("suspend", cards=cids)
        return bool(res)

    async def unsuspend_cards(self, cids: list[int]) -> bool:
        if not cids:
            return True
        res = await self._invoke("unsuspend", cards=cids)
        return bool(res)

    async def get_model_styling(self, model_name: str) -> str:
        try:
            res = await self._invoke("modelStyling", modelName=model_name)
            if isinstance(res, dict):
                return res.get("css", "")
            return str(res)
        except Exception:
            return ""

    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        try:
            res = await self._invoke("modelTemplates", modelName=model_name)
            # AnkiConnect returns { "Card 1": { "Front": "...", "Back": "..." } }
            return res
        except Exception as e:
            self.logger.warning(f"[model] modelTemplates failed for '{model_name}': {e}")
            return {}

    async def gui_browse(self, query: str) -> bool:
        """Open the Anki browser via AnkiConnect's guiBrowse action."""
        try:
            await self._invoke("guiBrowse", query=query)
            return True
        except Exception as e:
            self.logger.error(f"Failed to open Anki browser: {e}")
            return False

    async def get_card_ids_for_arete_ids(self, arete_ids: list[str]) -> list[int]:
        """Resolve Arete IDs to CIDs, preserving topological input order."""
        if not arete_ids:
            return []
        try:
            cids_ordered: list[int] = []
            seen: set[int] = set()
            CHUNK = 50
            for start in range(0, len(arete_ids), CHUNK):
                chunk = arete_ids[start : start + CHUNK]
                actions = [
                    {"action": "findCards", "params": {"query": f"tag:{aid}"}} for aid in chunk
                ]
                results = await self._invoke("multi", actions=actions)
                for result in results:
                    if isinstance(result, list):
                        for cid in result:
                            if cid not in seen:
                                seen.add(cid)
                                cids_ordered.append(cid)
            return cids_ordered
        except Exception as e:
            self.logger.error(f"Failed to resolve arete IDs: {e}")
            return []

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
            self.logger.error(f"create_topo_deck failed: {e}")
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
