"""Deck, deck-config, and package import/export AnkiConnect actions."""

import anki.utils
from anki.exporting import AnkiPackageExporter
from anki.importing import AnkiPackageImporter
from anki.utils import int_time

from . import util


class DeckActions:
    """Deck management, deck config, and .apkg import/export actions."""

    def deckStatsToJson(self, due_tree):
        return {
            "deck_id": due_tree.deck_id,
            "name": due_tree.name,
            "new_count": due_tree.new_count,
            "learn_count": due_tree.learn_count,
            "review_count": due_tree.review_count,
            "total_in_deck": due_tree.total_in_deck,
        }

    def collectDeckTreeChildren(self, parent_node):
        allNodes = {parent_node.deck_id: parent_node}
        for child in parent_node.children:
            for deckId, childNode in self.collectDeckTreeChildren(child).items():
                allNodes[deckId] = childNode
        return allNodes

    @util.api()
    def deckNames(self):
        # What decks.allNames() returns, without its two deprecation warnings.
        names = self.decks().all_names_and_ids(skip_empty_default=False, include_filtered=True)
        return [name.name for name in names]

    @util.api()
    def deckNamesAndIds(self):
        return {deck: self.decks().id(deck) for deck in self.deckNames()}

    @util.api()
    def deckNameFromId(self, deckId):
        deck = self.collection().decks.get(deckId)
        if deck is None:
            raise Exception(f"deck was not found: {deckId}")
        return deck["name"]

    @util.api()
    def getDecks(self, cards):
        decks = {}
        for card in cards:
            did = self.database().scalar("select did from cards where id=?", card)
            if did is None:
                continue
            deck_obj = self.decks().get(did, default=False)
            if deck_obj is None:
                continue
            deck = deck_obj["name"]
            if deck in decks:
                decks[deck].append(card)
            else:
                decks[deck] = [card]
        return decks

    @util.api()
    def createDeck(self, deck):
        did = self.decks().id(deck)
        self.scheduleRefresh()
        return did

    @util.api()
    def changeDeck(self, cards, deck):
        col = self.collection()
        did = col.decks.id(deck)
        mod = int_time()
        usn = col.usn()

        scids = anki.utils.ids2str(cards)
        col.sched.remFromDyn(cards)
        col.db.execute(
            "update cards set usn=?, mod=?, did=? where id in " + scids,
            usn,
            mod,
            did,
        )
        self.scheduleRefresh()

    @util.api()
    def deleteDecks(self, decks, cardsToo=False):
        if not cardsToo:
            # Anki deletes a deck's cards with the deck, and its delete call takes no flag
            # to keep them, so a request to keep them is refused rather than ignored.
            raise Exception(
                "Since Anki 2.1.28 it's not possible to delete decks without deleting cards as well"
            )
        existing = [d for d in decks if d in self.deckNames()]
        for deck in existing:
            did = self.decks().id(deck)
            self.decks().remove([did])
        if existing:
            self.scheduleRefresh()

    @util.api()
    def getDeckConfig(self, deck):
        if deck not in self.deckNames():
            return False

        collection = self.collection()
        did = collection.decks.id(deck)
        return collection.decks.config_dict_for_deck_id(did)

    @util.api()
    def saveDeckConfig(self, config):
        collection = self.collection()

        config["id"] = int(config["id"])
        config["mod"] = int_time()
        config["usn"] = collection.usn()
        if config["id"] not in [c["id"] for c in collection.decks.all_config()]:
            return False
        try:
            collection.decks.update_config(config)
        except Exception:
            return False
        self.scheduleRefresh()
        return True

    @util.api()
    def setDeckConfigId(self, decks, configId):
        configId = int(configId)
        collection = self.collection()
        if configId not in [c["id"] for c in collection.decks.all_config()]:
            return False

        deck_dicts = []
        for deck in decks:
            did = collection.decks.id(deck, create=False)
            if not did:
                return False
            deck_dict = collection.decks.get(did, default=False)
            if deck_dict is None or deck_dict.get("dyn"):
                return False
            deck_dicts.append(deck_dict)

        for deck_dict in deck_dicts:
            try:
                collection.decks.set_config_id_for_deck_dict(deck_dict, configId)
            except Exception:
                return False

        if deck_dicts:
            self.scheduleRefresh()
        return True

    @util.api()
    def cloneDeckConfigId(self, name, cloneFrom="1"):
        configId = int(cloneFrom)
        collection = self.collection()
        if configId not in [c["id"] for c in collection.decks.all_config()]:
            return False

        config = collection.decks.get_config(configId)
        return collection.decks.add_config_returning_id(name, config)

    @util.api()
    def removeDeckConfigId(self, configId):
        collection = self.collection()
        if int(configId) not in [c["id"] for c in collection.decks.all_config()]:
            return False

        collection.decks.remove_config(configId)
        self.scheduleRefresh()
        return True

    @util.api()
    def getDeckStats(self, decks):
        collection = self.collection()
        scheduler = self.scheduler()
        responseDict = {}
        deckIds = [collection.decks.id(d) for d in decks]

        allDeckNodes = self.collectDeckTreeChildren(scheduler.deck_due_tree())
        for deckId, deckNode in allDeckNodes.items():
            if deckId in deckIds:
                responseDict[deckId] = self.deckStatsToJson(deckNode)
        return responseDict

    @util.api()
    def exportPackage(self, deck, path, includeSched=False):
        collection = self.collection()
        if collection is not None:
            deck = collection.decks.by_name(deck)
            if deck is not None:
                exporter = AnkiPackageExporter(collection)
                exporter.did = deck["id"]
                exporter.includeSched = includeSched
                exporter.exportInto(path)
                return True

        return False

    @util.api()
    def importPackage(self, path):
        collection = self.collection()
        if collection is not None:
            importer = AnkiPackageImporter(collection, path)
            importer.run()
            self.scheduleRefresh()
            return True

        return False
