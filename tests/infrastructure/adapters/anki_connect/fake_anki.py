"""An in-memory AnkiConnect server for request-count tests.

It answers the actions the adapter sends, with the reply shapes of the
``arete_ankiconnect`` add-on: a request whose ``version`` is 4 or lower (the default for
a ``multi`` sub-action that names none) gets the bare result, any other gets
``{"result", "error"}``, and an error is always ``{"result": None, "error"}``.
``requests`` counts HTTP requests by action; ``sub_actions`` counts what ran inside
``multi``. Plug it in with ``adapter._http_request = fake.http``.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from typing import Any


class FakeAnki:
    def __init__(self, models: dict[str, list[str]] | None = None):
        """Start with an empty collection holding only the Default deck."""
        self.models = models or {
            "Basic": ["Front", "Back", "_obsidian_source"],
            "Cloze": ["Text", "Back Extra", "_obsidian_source"],
        }
        self.decks: set[str] = {"Default"}
        self.notes: dict[int, dict[str, Any]] = {}
        self.card_deck: dict[int, str] = {}
        self.requests: Counter[str] = Counter()
        self.sub_actions: Counter[str] = Counter()
        self.log: list[tuple[str, dict]] = []
        # fail(action, params) -> error text or None: inject an add-on error.
        self.fail: Callable[[str, dict], str | None] = lambda action, params: None
        self._next_id = 1000

    # -- state helpers -------------------------------------------------------

    def add(
        self,
        fields: dict[str, str],
        deck: str = "Default",
        model: str = "Basic",
        tags: list[str] | None = None,
        n_cards: int = 1,
    ) -> int:
        self._next_id += 1
        nid = self._next_id
        self.decks.add(deck)
        names = self.models[model]
        self.notes[nid] = {
            "model": model,
            "fields": {name: fields.get(name, "") for name in names},
            "tags": list(tags or []),
            "cards": [nid * 10 + i for i in range(n_cards)],
        }
        for cid in self.notes[nid]["cards"]:
            self.card_deck[cid] = deck
        return nid

    def reset_counts(self) -> None:
        self.requests.clear()
        self.sub_actions.clear()
        self.log.clear()

    # -- transport -----------------------------------------------------------

    async def http(self, payload: dict) -> Any:
        self.requests[payload["action"]] += 1
        await asyncio.sleep(0)  # a real transport yields, so concurrent callers interleave
        return self._handle(payload)

    def _handle(self, request: dict) -> Any:
        action = request.get("action", "")
        version = request.get("version", 4)
        params = request.get("params", {})
        self.log.append((action, params))
        try:
            error = self.fail(action, params)
            if error is not None:
                raise Exception(error)
            result = getattr(self, f"_a_{action}")(**params)
        except Exception as e:
            return {"result": None, "error": str(e)}
        return result if version <= 4 else {"result": result, "error": None}

    # -- actions -------------------------------------------------------------

    def _a_version(self):
        return 6

    def _a_multi(self, actions):
        replies = []
        for sub in actions:
            self.sub_actions[sub["action"]] += 1
            replies.append(self._handle(sub))
        return replies

    def _a_createDeck(self, deck):
        self.decks.add(deck)
        return 1

    def _a_deckNames(self):
        return sorted(self.decks)

    def _a_modelFieldNames(self, modelName):
        return list(self.models[modelName])

    def _a_modelFieldAdd(self, modelName, fieldName):
        self.models[modelName].append(fieldName)

    def _note_info(self, nid):
        note = self.notes.get(nid)
        if note is None:
            return {}
        names = self.models[note["model"]]
        return {
            "noteId": nid,
            "tags": list(note["tags"]),
            "fields": {n: {"value": note["fields"][n], "order": i} for i, n in enumerate(names)},
            "modelName": note["model"],
            "cards": list(note["cards"]),
        }

    def _a_notesInfo(self, notes):
        return [self._note_info(nid) for nid in notes]

    def _a_findNotes(self, query=None):
        if query is None:
            return []
        if m := re.fullmatch(r"tag:(\S+)", query):
            return [nid for nid, n in self.notes.items() if m.group(1) in n["tags"]]
        if m := re.fullmatch(r'"note:(.+)"', query):
            return [nid for nid, n in self.notes.items() if n["model"] == m.group(1)]
        if m := re.fullmatch(r'"deck:(.+)"', query):
            deck = m.group(1)
            return [
                nid
                for nid, n in self.notes.items()
                if any(
                    self.card_deck[c] == deck or self.card_deck[c].startswith(deck + "::")
                    for c in n["cards"]
                )
            ]
        raise Exception(f"fake cannot search {query!r}")

    def _a_addNote(self, note):
        if note["deckName"] not in self.decks:
            raise Exception("deck was not found: {}".format(note["deckName"]))
        return self.add(note["fields"], note["deckName"], note["modelName"], note["tags"])

    def _a_updateNoteFields(self, note):
        held = self.notes.get(note["id"])
        if held is None:
            raise Exception("note was not found: {}".format(note["id"]))
        for name, value in note["fields"].items():
            if name in held["fields"]:
                held["fields"][name] = value

    def _a_addTags(self, notes, tags, add=True):
        for nid in notes:
            held = self.notes[nid]["tags"]
            for tag in tags.split():
                if add and tag not in held:
                    held.append(tag)
                if not add and tag in held:
                    held.remove(tag)

    def _a_removeTags(self, notes, tags):
        return self._a_addTags(notes, tags, False)

    def _a_changeDeck(self, cards, deck):
        self.decks.add(deck)
        for cid in cards:
            self.card_deck[cid] = deck

    def _a_getDecks(self, cards):
        decks: dict[str, list[int]] = {}
        for cid in cards:
            decks.setdefault(self.card_deck[cid], []).append(cid)
        return decks

    def _a_deleteNotes(self, notes):
        for nid in notes:
            for cid in self.notes.pop(nid)["cards"]:
                del self.card_deck[cid]

    def _a_deleteDecks(self, decks, cardsToo=False):
        for deck in decks:
            self.decks.discard(deck)
