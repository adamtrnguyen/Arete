from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from arete.domain.constants import (
    CHUNK_SIZE,
    REQUEST_TIMEOUT,
    RESPONSIVENESS_TIMEOUT,
    SYNC_CONCURRENCY,
)
from arete.domain.interfaces import AnkiBridge
from arete.domain.models import (
    AddonOutdated,
    HeldCard,
    HeldNote,
    NewNote,
    NoteChange,
    NoteTypeInfo,
    OutcomeUnknown,
)
from arete.infrastructure.anki.search import search_term

# Writes sent in one multi request. Every write resets Anki's UI on its main thread, so
# one request holds no more than a consumer batch.
_WRITES_PER_REQUEST = 50

# Whether an action may be sent again after its reply was lost (a timeout, a dropped
# connection): True where running it twice leaves Anki as running it once does, as the
# add-on implements it. An action missing here is never sent again. A multi is as safe as
# its least safe action.
_RESEND_SAFE: dict[str, bool] = {
    # reads
    "version": True,
    "deckNames": True,
    "findNotes": True,
    "findCards": True,
    "notesInfo": True,
    "modelTemplates": True,
    "modelStyling": True,
    "getModelInfo": True,
    "getHomeDecks": True,
    # writes that set a state: a second run finds it set
    "createDeck": True,  # decks.id() returns the existing deck
    "setHomeDeck": True,  # sets the home deck of the listed cards
    "updateNoteFields": True,  # sets the listed fields
    "addTags": True,
    "removeTags": True,
    "modelFieldAdd": True,  # adds the field only if the type lacks it
    "suspend": True,  # sets the queue; a second run answers False
    "unsuspend": True,
    "deleteNotes": True,
    "deleteDecks": True,  # skips a deck that no longer exists
    "createFilteredDeck": True,  # empties and rebuilds the same deck
    "guiBrowse": True,
    # writes that are not: a second addNote makes a second note (or is refused as a
    # duplicate although the first landed); a second changeNoteType re-maps fields that
    # were mapped for the old type onto the converted note
    "addNote": False,
    "changeNoteType": False,
}

# Failures that prove the request never reached Anki: no connection was made.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_ATTEMPTS = 3
_RETRY_DELAY = 1.0  # seconds before sending again after a timeout
# A refused connection almost always means Anki is not running, and the caller falls back
# to the closed-Anki path only once this gives up; so it waits only briefly.
_NOT_SENT_DELAY = 0.1


def _is_resend_safe(action: str, params: dict) -> bool:
    if action == "multi":
        subs = params.get("actions") or []
        return all(_is_resend_safe(s.get("action", ""), s.get("params") or {}) for s in subs)
    return _RESEND_SAFE.get(action, False)


def _unknown_text(action: str, how: str) -> str:
    return f"AnkiConnect '{action}' {how}: Anki may or may not have applied it"


def _outdated(action: str, error: object) -> AddonOutdated | None:
    """The error to stop the sync with, when the add-on answered that it lacks the action."""
    if isinstance(error, Exception) and "unsupported action" in str(error):
        return AddonOutdated(
            f"The Anki add-on lacks the '{action}' action the sync needs. Update the "
            "arete_ankiconnect add-on and restart Anki."
        )
    return None


class ConnectNoteSession:
    """`NoteStoreSession` over AnkiConnect, batched: a few requests per call, any size.

    Reads go 500 ids per request; writes go in `multi` requests of at most
    `_WRITES_PER_REQUEST`, step by step (`apply_changes`). Note types come from
    `getModelInfo`, home decks from `getHomeDecks`, and a card is filed with `setHomeDeck`,
    which leaves a queued card in its filtered deck.

    An add-on that answers "unsupported action" to one of those three is too old for the
    sync: the reads raise `AddonOutdated`, and a filing fails its note with it.
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
        """A step's final error: a lost retype is confirmed, an unknown action named."""
        if error is None:
            return None
        action, _params = step
        if action == "changeNoteType":
            return await self._confirm_retype(change, error)
        return _outdated(action, error) or error

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


def _model_info(name: str, info: Any) -> NoteTypeInfo | None:
    """A note type from `getModelInfo`: kind 1 is cloze."""
    if not isinstance(info, dict):
        return None
    fields = tuple(str(f) for f in info.get("fields") or [])
    return NoteTypeInfo(name, fields, info.get("type") == 1, int(info.get("templates") or 0))


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

    def __init__(self, url: str = "http://127.0.0.1:8765"):
        """Initialize with AnkiConnect URL, auto-detecting WSL bridge if needed."""
        self.logger = logging.getLogger(__name__)
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

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ConnectNoteSession]:
        """Note reads and writes over AnkiConnect (`NoteStorePort`); nothing to open."""
        yield ConnectNoteSession(self)

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
        """Send writes in one request, a lone one as itself; per write its error or None.

        With `results`, per write its result, or its error.
        """
        if len(writes) == 1:
            action, params = writes[0]
            try:
                value = await self._invoke(action, **params)
                return [value if results else None]
            except Exception as e:
                return [e]
        actions = [{"action": a, "version": 6, "params": p} for a, p in writes]
        try:
            replies = await self._invoke("multi", actions=actions)
            if not isinstance(replies, list) or len(replies) != len(writes):
                raise ValueError(f"multi answered {replies!r:.80} for {len(writes)} actions")
        except Exception as e:
            return [e] * len(writes)
        errors: list = []
        for reply in replies:
            if not isinstance(reply, dict) or set(reply) != {"result", "error"}:
                errors.append(ValueError(f"multi sub-reply is malformed: {reply!r:.80}"))
            elif reply["error"] is not None:
                errors.append(Exception(reply["error"]))
            else:
                errors.append(reply["result"] if results else None)
        return errors

    async def _confirm_retype(self, nid: int, new_model: str, lost: OutcomeUnknown) -> None:
        """Count a ``changeNoteType`` with a lost reply as done if the note has the new type.

        It is never sent a second time: its field map indexes the old type's fields, and
        on a note already converted it would move them again. Otherwise this raises.
        """
        try:
            [held] = await self._invoke("notesInfo", notes=[nid])
        except Exception as e:
            raise OutcomeUnknown(f"{lost}. Re-reading note {nid} failed: {e}") from lost
        if held.get("modelName") != new_model:
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
        """Send one action and return its result; an error reply raises.

        A request that provably never left (no connection) is sent again, up to
        ``_ATTEMPTS`` times. A timeout is sent again only for an action ``_RESEND_SAFE``
        allows. For any other action, a timeout, a dropped connection or an unreadable
        reply raises ``OutcomeUnknown``: Anki may have applied it.
        """
        payload = {"action": action, "version": 6, "params": params}
        resend_safe = _is_resend_safe(action, params)
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                async with self._invoke_sem:
                    data = await asyncio.wait_for(
                        self._http_request(payload), timeout=REQUEST_TIMEOUT
                    )
            except _NOT_SENT as exc:
                self.logger.warning(
                    f"AnkiConnect '{action}' not sent: {exc} (attempt {attempt}/{_ATTEMPTS})"
                )
                if attempt == _ATTEMPTS:
                    raise
                await asyncio.sleep(_NOT_SENT_DELAY)
                continue
            except (TimeoutError, httpx.TimeoutException) as exc:
                self.logger.warning(
                    f"AnkiConnect timeout on '{action}' (attempt {attempt}/{_ATTEMPTS})"
                )
                # The client stays open: it is shared with the requests in flight beside
                # this one. The timed-out request's own connection closed when wait_for
                # cancelled it.
                if not resend_safe:
                    raise OutcomeUnknown(_unknown_text(action, "timed out")) from exc
                if attempt == _ATTEMPTS:
                    raise TimeoutError(
                        f"AnkiConnect timeout on '{action}' after {_ATTEMPTS} attempts"
                    ) from exc
            except Exception as e:
                self.logger.error(f"AnkiConnect call failed: {e}")
                if not resend_safe:
                    raise OutcomeUnknown(_unknown_text(action, f"failed in transit ({e})")) from e
                raise
            else:
                return self._result_of(action, data, resend_safe)
            await asyncio.sleep(_RETRY_DELAY)

    def _result_of(self, action: str, data: Any, resend_safe: bool) -> Any:
        """The result of a reply. An error reply raises; so does a malformed one."""
        problem = None
        if len(data) != 2:
            problem = "response has an unexpected number of fields"
        elif "error" not in data:
            problem = "response is missing required error field"
        elif "result" not in data:
            problem = "response is missing required result field"
        if problem is not None:
            self.logger.error(f"AnkiConnect call failed: {problem}")
            if not resend_safe:
                raise OutcomeUnknown(_unknown_text(action, problem))
            raise ValueError(problem)
        if data["error"] is not None:
            self.logger.error(f"AnkiConnect call failed: {data['error']}")
            raise Exception(data["error"])
        return data["result"]

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
        deck_filter = f"{search_term('deck', deck_name)} " if deck_name else ""
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
                    {"action": "findCards", "params": {"query": search_term("tag", aid)}}
                    for aid in chunk
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
