"""Custom Arete actions: filtered queues, home decks, model metadata, retype, FSRS stats."""

import logging

import anki.utils
from anki.cards import Card
from anki.errors import NotFoundError

from . import util

logger = logging.getLogger("arete_ankiconnect")


class AreteActions:
    """Arete-specific AnkiConnect actions for sync, topological queues, and FSRS stats."""

    @util.api()
    def createFilteredDeck(self, name, cids, reschedule=True):
        """Create or rebuild a filtered deck with cards in the specified CID order."""
        if not isinstance(cids, list) or not cids:
            raise Exception("No card IDs provided")
        for cid in cids:
            if type(cid) is not int:
                raise Exception(f"invalid card id: {cid!r}")

        col = self.collection()

        # 1. Find or create filtered deck
        did = col.decks.id(name, create=False)
        if did:
            deck = col.decks.get(did)
            if not deck or not deck.get("dyn"):
                raise Exception(f"Deck '{name}' exists and is not a filtered deck")
            col.sched.empty_filtered_deck(did)
        else:
            did = col.decks.new_filtered(name)

        # 2. Configure search terms
        deck = col.decks.get(did)
        if deck is None:
            raise Exception(f"Deck '{name}' could not be loaded")
        query = " OR ".join(f"cid:{cid}" for cid in cids)
        deck["terms"] = [[query, len(cids), 0]]  # [query, limit, order=oldest]
        deck["resched"] = reschedule
        col.decks.save(deck)

        # 3. Rebuild (pull cards into filtered deck)
        col.sched.rebuild_filtered_deck(did)

        # 4. Enforce topological order via due values.
        # One update for all cards; a repeated cid keeps its last position.
        # Intraday learning cards (queue == 1) store a Unix timestamp in `due`,
        # so leave their `due` untouched.
        pulled: dict[int, Card] = {}
        for i, cid in enumerate(cids):
            try:
                card = col.get_card(cid)
            except NotFoundError:
                continue  # Card may not exist
            if card.did == did and card.queue != 1:
                card.due = i + 1000
                pulled[card.id] = card

        if pulled:
            try:
                col.update_cards(list(pulled.values()), skip_undo_entry=True)
            except Exception as e:
                logger.warning("createFilteredDeck batch update_cards failed: %s", e)
                for card in pulled.values():
                    try:
                        col.update_card(card, skip_undo_entry=True)
                    except Exception as item_err:
                        logger.warning(
                            "createFilteredDeck update_card(%s) failed: %s",
                            card.id,
                            item_err,
                        )

        self.scheduleRefresh()
        return did

    @util.api()
    def getHomeDecks(self, cards):
        """Per card id, in order: the deck it belongs to and the filtered deck it sits in.

        `{"homeDeck": name, "filteredDeck": name or None}`; a card queued in a filtered
        deck belongs to the deck it returns to (`odid`). A deck that no longer exists has
        the name None. A card the collection does not hold gets None. One query.
        """
        ids = [cid for cid in cards if type(cid) is int]
        rows = {}
        if ids:
            for cid, did, odid in self.database().all(
                f"select id, did, odid from cards where id in {anki.utils.ids2str(ids)}"
            ):
                rows[cid] = (did, odid)
        names = {}

        def name(did):
            if did not in names:
                deck = self.decks().get(did, default=False)
                names[did] = deck["name"] if deck else None
            return names[did]

        result = []
        for cid in cards:
            if type(cid) is not int or cid not in rows:
                result.append(None)
                continue
            did, odid = rows[cid]
            if odid:
                result.append({"homeDeck": name(odid), "filteredDeck": name(did)})
            else:
                result.append({"homeDeck": name(did), "filteredDeck": None})
        return result

    @util.api()
    def setHomeDeck(self, cards, deck):
        """File the cards under an existing deck without taking any out of a filtered deck.

        A card queued in a filtered deck gets the deck as the one it returns to (`odid`)
        and stays queued; any other card moves to it (`did`). Due dates, queues and the
        rest of the scheduling are untouched.
        """
        col = self.collection()
        did = col.decks.id(deck, create=False)
        deck_dict = col.decks.get(did, default=False) if did else None
        if not did or deck_dict is None:
            raise Exception(f"deck was not found: {deck}")
        if deck_dict.get("dyn"):
            raise Exception(f"cannot set a filtered deck as home deck: {deck}")

        queued: dict[int, Card] = {}
        loose: list[int] = []
        seen_loose: set[int] = set()
        for cid in cards:
            if type(cid) is not int:
                raise Exception(f"invalid card id: {cid!r}")
            card = self.getCard(cid)
            if card.odid:
                if card.odid != did and card.id not in queued:
                    card.odid = did
                    queued[card.id] = card
            elif card.did != did and card.id not in seen_loose:
                seen_loose.add(card.id)
                loose.append(card.id)

        if queued:
            col.update_cards(list(queued.values()), skip_undo_entry=True)
        if loose:
            col.set_deck(loose, int(did))
        if queued or loose:
            self.scheduleRefresh()

    @util.api()
    def getModelInfo(self, models):
        """Per note type: canonical name, kind (0 standard, 1 cloze), fields, template count.

        A name the collection does not hold gets None.
        """
        result = {}
        for modelName in models:
            model = self.collection().models.by_name(modelName)
            if model is None:
                result[modelName] = None
                continue
            result[modelName] = {
                "name": model["name"],
                "type": model["type"],
                "fields": [field["name"] for field in model["flds"]],
                "templates": len(model["tmpls"]),
            }
        return result

    @util.api()
    def changeNoteType(self, notes, modelName, newFields):
        """Convert notes of one type to modelName in place; cards and reviews are kept.

        newFields: for each field of modelName, the index of the old field it takes, or -1.
        """
        if not notes:
            raise Exception("no notes provided")
        col = self.collection()
        new_type = col.models.by_name(modelName)
        if new_type is None:
            raise Exception(f"model was not found: {modelName}")
        if not isinstance(newFields, list) or len(newFields) != len(new_type["flds"]):
            raise Exception(
                f"newFields length must match {modelName} field count ({len(new_type['flds'])})"
            )
        old_ids = {col.get_note(int(nid)).mid for nid in notes}
        if len(old_ids) != 1:
            raise Exception("changeNoteType takes notes of one type at a time")
        old_id = old_ids.pop()
        if old_id == new_type["id"] and newFields == list(range(len(new_type["flds"]))):
            return True
        info = col.models.change_notetype_info(
            old_notetype_id=old_id, new_notetype_id=new_type["id"]
        )
        request = info.input
        request.note_ids.extend(int(nid) for nid in notes)
        request.new_fields[:] = newFields
        col.models.change_notetype_of_notes(request)
        self.scheduleRefresh()
        return True

    @util.api()
    def getFSRSStats(self, cards=None):
        """FSRS memory state per card: difficulty (1-10) and stability (days), or None."""
        col = self.collection()
        result = []
        for cid in cards or []:
            item = {"cardId": cid, "difficulty": None, "stability": None}
            try:
                int_cid = int(cid)
                item["cardId"] = int_cid
                state = col.get_card(int_cid).memory_state
            except Exception as e:
                item["error"] = str(e)
                state = None
            if state:
                item["difficulty"] = state.difficulty
                item["stability"] = state.stability
            result.append(item)
        return result
