"""An in-memory AnkiConnect server for request-count tests.

It answers the actions the adapter sends, with the reply shapes of the
``arete_ankiconnect`` add-on: a request whose ``version`` is 4 or lower (the default for
a ``multi`` sub-action that names none) gets the bare result, any other gets
``{"result", "error"}``, and an error is always ``{"result": None, "error"}``.
``requests`` counts HTTP requests by action; ``sub_actions`` counts what ran inside
``multi``. Plug it in with ``adapter._http_request = fake.http``.

``interrupt`` cuts a request off in transit: ``(applied, exc)`` runs the action first
when ``applied`` is true, then raises ``exc`` where the reply would be. A ``TimeoutError``
there is what ``asyncio.wait_for`` raises when Anki answers too late.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from arete.domain.identity import anki_tags


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
        # interrupt(action, params) -> (applied, exception) or None: lose the reply.
        self.interrupt: Callable[[str, dict], tuple[bool, BaseException] | None] = (
            lambda action, params: None
        )
        self.templates: dict[str, int] = {}  # model -> number of card templates (default 1)
        self.cloze_models: set[str] = {"Cloze"}
        # card -> the deck it returns to, while `card_deck` names the filtered deck it sits in
        self.card_home: dict[int, str] = {}
        # actions answered "unsupported action", as an add-on without them answers
        self.missing_actions: set[str] = set()
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
            "tags": anki_tags(tags or []),  # as Anki stores them: `A B` is two tags
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
        cut = self.interrupt(payload["action"], payload.get("params", {}))
        if cut is not None:
            applied, exc = cut
            if applied:
                self._handle(payload)
            raise exc
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
            method = getattr(self, f"_a_{action}", None)
            if method is None or action in self.missing_actions:
                raise Exception("unsupported action")
            result = method(**params)
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

    def _a_modelFieldAdd(self, modelName, fieldName):
        if modelName not in self.models:
            raise Exception(f"model was not found: {modelName}")
        if fieldName not in self.models[modelName]:  # the add-on adds a missing field only
            self.models[modelName].append(fieldName)

    def _a_modelTemplates(self, modelName):
        """One template per card, as Anki's: a cloze type's front holds its cloze field."""
        if modelName not in self.models:
            raise Exception(f"model was not found: {modelName}")
        if modelName in self.cloze_models:
            first = self.models[modelName][0]
            return {"Cloze": {"Front": f"{{{{cloze:{first}}}}}", "Back": f"{{{{cloze:{first}}}}}"}}
        n = self.templates.get(modelName, 1)
        return {f"Card {i + 1}": {"Front": "", "Back": ""} for i in range(n)}

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
        if m := re.fullmatch(r"tag:(\S+)", query):  # Anki matches tags ignoring case
            tag = m.group(1).casefold()
            return [
                nid for nid, n in self.notes.items() if tag in {t.casefold() for t in n["tags"]}
            ]
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
        if note["modelName"] not in self.models:
            raise Exception("model was not found: {}".format(note["modelName"]))
        if note["deckName"] not in self.decks:
            raise Exception("deck was not found: {}".format(note["deckName"]))
        self._refuse_duplicate(note)
        return self.add(note["fields"], note["deckName"], note["modelName"], note["tags"])

    def _refuse_duplicate(self, note: dict) -> None:
        """The add-on's check (``createNote``, ``isNoteDuplicateOrEmptyInScope``).

        The note type's first field, HTML stripped, against every note of the same type;
        with ``duplicateScope: "deck"`` only notes with a card in the target deck count.
        """
        options = note.get("options", {})
        first = self.models[note["modelName"]][0]
        raw = note["fields"].get(first, "")
        if not raw.strip():
            raise Exception("cannot create note because it is empty")
        value = _checksum_text(raw)
        if options.get("allowDuplicate", False):
            return
        in_deck = options.get("duplicateScope") == "deck"
        for held in self.notes.values():
            if held["model"] != note["modelName"]:
                continue
            if _checksum_text(held["fields"].get(first, "")) != value:
                continue
            if not in_deck or any(self.card_deck[c] == note["deckName"] for c in held["cards"]):
                raise Exception("cannot create note because it is a duplicate")

    def _a_changeNoteType(self, notes, modelName, newFields):
        """The add-on's changeNoteType: ``newFields[i]`` indexes the note's current fields."""
        if modelName not in self.models:
            raise Exception(f"model was not found: {modelName}")
        if len({self.notes[int(nid)]["model"] for nid in notes}) != 1:
            raise Exception("changeNoteType takes notes of one type at a time")
        for nid in notes:
            held = self.notes[int(nid)]
            old = [held["fields"][name] for name in self.models[held["model"]]]
            held["fields"] = {
                name: old[i] if i >= 0 else ""
                for name, i in zip(self.models[modelName], newFields, strict=True)
            }
            held["model"] = modelName
        return True

    def _a_updateNoteFields(self, note):
        held = self.notes.get(note["id"])
        if held is None:
            raise Exception("note was not found: {}".format(note["id"]))
        for name, value in note["fields"].items():
            if name in held["fields"]:
                held["fields"][name] = value

    def _a_addTags(self, notes, tags):
        """As Anki: the string is split and each tag stored as `anki_tags` says."""
        for nid in notes:
            held = self.notes[nid]["tags"]
            for tag in anki_tags([tags]):
                if tag.casefold() not in {t.casefold() for t in held}:
                    held.append(tag)

    def _a_removeTags(self, notes, tags):
        """As Anki: the names are split at spaces and removed ignoring case."""
        gone = {t.casefold() for t in re.split("[ 　]", tags) if t}
        for nid in notes:
            self.notes[nid]["tags"] = [
                t for t in self.notes[nid]["tags"] if t.casefold() not in gone
            ]

    def queue(self, nids, deck="Arete::Queue"):
        """Pull the notes' cards into a filtered deck, as createFilteredDeck does."""
        self.decks.add(deck)
        for nid in nids:
            for cid in self.notes[nid]["cards"]:
                self.card_home.setdefault(cid, self.card_deck[cid])
                self.card_deck[cid] = deck

    def _a_getHomeDecks(self, cards):
        out = []
        for cid in cards:
            if cid not in self.card_deck:
                out.append(None)
            elif cid in self.card_home:
                out.append({"homeDeck": self.card_home[cid], "filteredDeck": self.card_deck[cid]})
            else:
                out.append({"homeDeck": self.card_deck[cid], "filteredDeck": None})
        return out

    def _a_setHomeDeck(self, cards, deck):
        if deck not in self.decks:
            raise Exception(f"deck was not found: {deck}")
        for cid in cards:
            if cid in self.card_home:
                self.card_home[cid] = deck
            else:
                self.card_deck[cid] = deck

    def _a_getModelInfo(self, models):
        return {
            name: {
                "type": 1 if name in self.cloze_models else 0,
                "fields": list(self.models[name]),
                "templates": 1 if name in self.cloze_models else self.templates.get(name, 1),
            }
            if name in self.models
            else None
            for name in models
        }

    def _a_deleteNotes(self, notes):
        for nid in notes:
            for cid in self.notes.pop(nid)["cards"]:
                del self.card_deck[cid]

    def _a_deleteDecks(self, decks, cardsToo=False):
        for deck in decks:
            self.decks.discard(deck)


def _checksum_text(value: str) -> str:
    """What Anki's field checksum compares: the field without its HTML."""
    return re.sub(r"<[^>]+>", "", value)
